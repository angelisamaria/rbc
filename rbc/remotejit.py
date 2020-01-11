# Author: Pearu Peterson
# Created: February 2019

__all__ = ['RemoteJIT', 'Signature', 'Caller']

import os
import inspect

from . import irtools
from .typesystem import Type
from .thrift import Server, Dispatcher, dispatchermethod, Data, Client
from .utils import get_local_ip
from .targetinfo import TargetInfo


class Signature(object):
    """Signature decorator for Python functions.

    A Signature decorator may contain many signature objects
    representing the prototypes of functions.

    Signature decorators are re-usable and composeable. For example:

      rjit = RemoteJIT(host=..., port=...)

      remotebinaryfunc = rjit('int32(int32, int32)',
                              'float32(float32, float32)', ...)
      # remotebinaryfunc is Signature instance

      @remotebinaryfunc
      def add(a, b):
          return a + b
      # add will be Caller instance

      @remotebinaryfunc
      def sub(a, b):
          return a - b
      # sub will be Caller instance

      add(1, 2) -> 3
      sub(1, 2) -> -1
    """

    def __init__(self, remotejit, options=None):
        self.remotejit = remotejit   # RemoteJIT
        self.signatures = []
        if options is None:
            options = remotejit.options
        self.options = options

    def __str__(self):
        lst = ["'%s'" % (s,) for s in self.signatures]
        return '%s(%s)' % (self.__class__.__name__, ', '.join(lst))

    def __call__(self, obj):
        """Decorate signatures or a function.

        Parameters
        ----------
        obj : {str, Signature, function, ...}
          Specify object that represents a function type.

        Returns
        -------
        result : {Signature, Caller}
          If obj is a function, return Caller. Otherwise return self
          that is extended with new signatures from obj.

        Note
        ----

        The validity of the input argument is not checked here.  This
        is because the bit-size of certain C types (e.g. size_t, long,
        etc) depend on the target device which information will be
        available at the compile stage. The target dependent
        signatures can be retrieved using
        `signature.get_signatures(target_info)`.

        """
        if obj is None:
            return self
        if isinstance(obj, Signature):
            self.signatures.extend(obj.signatures)
            self.remotejit.discard_last_compile()
            return self
        if isinstance(obj, Caller):
            # return new Caller with extended signatures set
            assert obj.remotejit is self.remotejit
            final = Signature(self.remotejit, options=self.options)
            final(self)  # copies the signatures from self to final
            final(obj.signature)  # copies the signatures from obj to final
            return Caller(obj.func, final)
        if inspect.isfunction(obj):
            final = Signature(self.remotejit, options=self.options)
            final(self)  # copies the signatures from self to final
            return Caller(obj, final)
        self.signatures.append(obj)
        self.remotejit.discard_last_compile()
        return self

    def best_match(self, func, atypes: tuple, target_info: TargetInfo) -> Type:
        """Return function type from signatures that matches best with given
        argument types.

        If no match is found, raise TypeError.

        Parameters
        ----------
        atypes : Type-tuple
          Specify a tuple of argument types.
        target_info : TargetInfo
          Specify target device information.

        Returns
        -------
        ftype : Type
          Function type that arguments match best with given argument
          types.
        """
        ftype = None
        match_penalty = None
        available_types = self.normalized(func, target_info).signatures
        for typ in available_types:
            penalty = typ.match(atypes)
            if penalty is not None:
                if ftype is None or penalty < match_penalty:
                    ftype = typ
                    match_penalty = penalty
        if ftype is None:
            satypes = ', '.join(map(str, atypes))
            available = '; '.join(map(str, available_types))
            raise TypeError(
                f'found no matching function type to given argument types'
                f' `{satypes}`. Available function types: {available}')
        return ftype

    def normalized(self, func, target_info: TargetInfo):
        """Return a copy of Signature object where all signatures are
        normalized to Type instances using given target device
        information.

        Parameters
        ----------
        func : function
          Python function that annotations are attached to signature.
        target_info : TargetInfo

        Returns
        -------
        signature : Signature
        """
        signature = Signature(self.remotejit, options=self.options)
        fsig = Type.fromcallable(func, target_info)
        nargs = len(fsig[1])
        for sig in self.signatures:
            if nargs is None:
                nargs = len(sig[1])
            sig = Type.fromobject(sig, target_info)
            if not sig.is_complete:
                continue
            if not sig.is_function:
                raise ValueError(
                    'expected signature representing function type,'
                    f' got `{sig}`')
            if len(sig[1]) != nargs:
                raise ValueError(f'signature `{sig}` must have arity {nargs}'
                                 f' but got {len(sig[1])}')
            if fsig is not None:
                sig.inherit_annotations(fsig)
            if sig not in signature.signatures:
                signature.signatures.append(sig)
        if fsig.is_complete:
            if fsig not in signature.signatures:
                signature.signatures.append(fsig)
        return signature


class Caller(object):
    """Remote JIT caller, holds the decorated function that can be
    executed remotely.
    """

    def __init__(self, func, signature: Signature, local=False):
        """Construct remote JIT caller instance.

        Parameters
        ----------
        func : callable
          Specify a Python function that is used as a template to
          remotely JIT compiled functions.
        signature : Signature
          Specify a collection of signatures.
        local : bool
          When True, local process will be interpreted as
          remote. Useful for debugging.
        """
        self.remotejit = signature.remotejit
        self.signature = signature
        self.func = func
        self.nargs = len(inspect.signature(func).parameters)

        # Attributes used in RBC user-interface
        # cache of function types and LLVM IR strings
        self.local = local
        self.ir_cache = {}
        self._client = None

        self.remotejit.add_caller(self)

    @property
    def host(self):
        """Return Caller instance that executes function calls on the local
        host. Useful for debugging.
        """
        return Caller(self.func, self.signature, local=True)

    def __repr__(self):
        return '%s(%s, %s, local=%s)' % (type(self).__name__, self.func,
                                         self.signature, self.local)

    def __str__(self):
        return self.describe()

    def describe(self):
        lst = ['']
        for device, target_info in self.remotejit.targets.items():
            lst.append(f'{device:-^80}')
            signatures = self.get_signatures(target_info)
            llvm_module = irtools.compile_to_LLVM(
                [(self.func, signatures)],
                target_info,
                **self.remotejit.options)
            lst.append(str(llvm_module))
        lst.append(f'{"":-^80}')
        return '\n'.join(lst)

    @property
    def client(self):
        if self._client is None:
            if self.local:
                self._client = LocalClient()
            else:
                self._client = self.remotejit.make_client()
        return self._client

    def get_signatures(self, target_info=None):
        """Return a list of normalized signatures for given target device.
        """
        if target_info is None:
            target_info = list(self.remotejit.targets.values())[0]
        return self.signature.normalized(self.func, target_info).signatures

    # RBC user-interface

    def _remote_compile(self, ftype: Type):
        """Compile function and signatures to machine code in remote JIT server.
        """
        ir = self.ir_cache.get(ftype)
        if ir is not None:
            return
        target_info = list(self.remotejit.targets.values())[0]
        llvm_module = irtools.compile_to_LLVM([(self.func, [ftype])],
                                              target_info,
                                              **self.remotejit.options)
        ir = str(llvm_module)
        mangled_signatures = ';'.join([s.mangle() for s in [ftype]])
        response = self.client(remotejit=dict(
            compile=(self.func.__name__, mangled_signatures, ir)))
        assert response['remotejit']['compile']
        self.ir_cache[ftype] = ir

    def _remote_call(self, ftype: Type, arguments: tuple):
        fullname = self.func.__name__ + ftype.mangle()
        response = self.client(remotejit=dict(call=(fullname, arguments)))
        return response['remotejit']['call']

    def __call__(self, *arguments):
        """Return the result of a remote JIT compiled function call.
        """
        target_info = list(self.remotejit.targets.values())[0]
        atypes = tuple(map(Type.fromvalue, arguments))
        ftype = self.signature.best_match(self.func, atypes, target_info)
        self._remote_compile(ftype)
        return self._remote_call(ftype, arguments)


class RemoteJIT(object):
    """RemoteJIT is a decorator generator for user functions to be
    remotely JIT compiled.

    To use, define

      rjit = RemoteJIT(host=..., port=...)

    and then use

      @rjit
      def foo(a: int, b: int) -> int:
        return a + b

    or

      @rjit('double(double, double)',
            'int64(int64, int64)')
      def foo(a, b):
        return a + b

    Finally, call

      c = foo(1, 2)

    where the sum will be evaluated in the remote host.
    """

    converters = []

    multiplexed = True

    thrift_content = None

    def __init__(self, host='localhost', port=11530, **options):
        """Construct remote JIT function decorator.

        The decorator is re-usable for different functions.

        Parameters
        ----------
        host : str
          Specify the host name of IP of JIT server
        port : {int, str}
          Specify the service port of the JIT server
        options : dict
          Specify default options.
        """
        if host == 'localhost':
            host = get_local_ip()

        self.debug = options.pop('debug', False)
        self.host = host
        self.port = int(port)
        self.options = options
        self.server_process = None

        # A collection of Caller instances. Each represents a function
        # that have many argument type dependent implementations.
        self._callers = []
        self._last_ir_map = None
        self._targets = None

    def add_caller(self, caller):
        self._callers.append(caller)
        self.discard_last_compile()

    def get_callers(self):
        return self._callers

    def reset(self):
        """Drop all callers definitions and compilation results.
        """
        self._callers.clear()
        self.discard_last_compile()

    @property
    def have_last_compile(self):
        return self._last_ir_map is not None

    def discard_last_compile(self):
        self._last_ir_map = None

    @property
    def targets(self):
        """Return device-target_info mapping of the remote server.
        """
        if self._targets is not None:
            return self._targets
        # TODO: retrieve target info from remote client
        self._targets = targets = dict(host_cpu=TargetInfo.host())
        for target in targets.values():
            for c in self.converters:
                target.add_converter(c)
        return targets

    def __call__(self, *signatures, **options):
        """Define a remote JIT function signatures and template.

        Parameters
        ----------
        signatures : tuple
          Specify signatures of a remote JIT function, or a Python
          function as a template from which the remote JIT function
          will be compiled.
        options : dict
          Specify options that overwide the default options.

        Returns
        -------
        sig: {Signature, Caller}
          Signature decorator or Caller

        Notes
        -----
        The signatures can be strings in the following form:

          "<return type>(<argument type 1>, <argument type 2>, ...)"

        or any other object that can be converted to function type,
        see `Type.fromobject` for more information.
        """
        s = Signature(self, options=options)
        for sig in signatures:
            s = s(sig)
        return s

    def start_server(self, background=False):
        thrift_file = os.path.join(os.path.dirname(__file__),
                                   'remotejit.thrift')
        print('staring rpc.thrift server: %s' % (thrift_file), end='')
        if background:
            ps = Server.run_bg(DispatcherRJIT, thrift_file,
                               dict(host=self.host, port=self.port))
            self.server_process = ps
        else:
            Server.run(DispatcherRJIT, thrift_file,
                       dict(host=self.host, port=self.port))
            print('... rpc.thrift server stopped')

    def stop_server(self):
        if self.server_process is not None and self.server_process.is_alive():
            print('... stopping rpc.thrift server')
            self.server_process.terminate()
            self.server_process = None

    def make_client(self):
        return Client(
            host=self.host,
            port=self.port,
            multiplexed=self.multiplexed,
            thrift_content=self.thrift_content,
            socket_timeout=10000)


class DispatcherRJIT(Dispatcher):
    """Implements remotejit service methods.
    """

    def __init__(self, server):
        Dispatcher.__init__(self, server)
        self.compiled_functions = dict()
        self.engines = dict()

    @dispatchermethod
    def compile(self, name: str, signatures: str, ir: str) -> int:
        """JIT compile function.

        Parameters
        ----------
        name : str
          Specify the function name.
        signatures : str
          Specify semi-colon separated list of mangled signatures.
        ir : str
          Specify LLVM IR representation of the function.
        """
        engine = irtools.compile_IR(ir)
        for msig in signatures.split(';'):
            sig = Type.demangle(msig)
            fullname = name + msig
            addr = engine.get_function_address(fullname)
            # storing engine as the owner of function addresses
            self.compiled_functions[fullname] = engine, sig.toctypes()(addr)
        return True

    @dispatchermethod
    def call(self, fullname: str, arguments: tuple) -> Data:
        """Call JIT compiled function

        Parameters
        ----------
        fullname : str
          Specify the full name of the function that is in form
          "<name><mangled signature>"
        arguments : tuple
          Speficy the arguments to the function.
        """
        ef = self.compiled_functions.get(fullname)
        if ef is None:
            raise RuntimeError('no such compiled function `%s`' % (fullname))
        r = ef[1](*arguments)
        if hasattr(r, 'topython'):
            return r.topython()
        return r


class LocalClient(object):
    """Pretender of thrift.Client.

    All calls will be made in a local process. Useful for debbuging.
    """

    def __init__(self, **options):
        self.options = options
        self.dispatcher = DispatcherRJIT(None)

    def __call__(self, **services):
        results = {}
        for service_name, query_dict in services.items():
            results[service_name] = {}
            for mthname, args in query_dict.items():
                mth = getattr(self.dispatcher, mthname)
                mth = inspect.unwrap(mth)
                results[service_name][mthname] = mth(self.dispatcher, *args)
        return results
