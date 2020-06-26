import pytest
import numpy as np
import rbc.omnisci_backend as omni  # noqa: F401


rbc_omnisci = pytest.importorskip('rbc.omniscidb')
available_version, reason = rbc_omnisci.is_available()
pytestmark = pytest.mark.skipif(not available_version, reason=reason)


@pytest.fixture(scope='module')
def omnisci():
    config = rbc_omnisci.get_client_config(debug=not True)
    m = rbc_omnisci.RemoteOmnisci(**config)
    table_name = 'rbc_test_omnisci_array'
    m.sql_execute('DROP TABLE IF EXISTS {table_name}'.format(**locals()))
    sqltypes = ['FLOAT[]', 'DOUBLE[]',
                'TINYINT[]', 'SMALLINT[]', 'INT[]', 'BIGINT[]',
                'BOOLEAN[]']
    # todo: TEXT ENCODING DICT, TEXT ENCODING NONE, TIMESTAMP, TIME,
    # DATE, DECIMAL/NUMERIC, GEOMETRY: POINT, LINESTRING, POLYGON,
    # MULTIPOLYGON, See
    # https://www.omnisci.com/docs/latest/5_datatypes.html
    colnames = ['f4', 'f8', 'i1', 'i2', 'i4', 'i8', 'b']
    table_defn = ',\n'.join('%s %s' % (n, t)
                            for t, n in zip(sqltypes, colnames))
    m.sql_execute(
        'CREATE TABLE IF NOT EXISTS {table_name} ({table_defn});'
        .format(**locals()))

    def row_value(row, col, colname):
        if colname == 'b':
            return 'ARRAY[%s]' % (', '.join(
                ("'true'" if i % 2 == 0 else "'false'")
                for i in range(-3, 3)))
        if colname.startswith('f'):
            return 'ARRAY[%s]' % (', '.join(
                str(row * 10 + i + 0.5) for i in range(-3, 3)))
        return 'ARRAY[%s]' % (', '.join(
            str(row * 10 + i) for i in range(-3, 3)))

    rows = 5
    for i in range(rows):
        table_row = ', '.join(str(row_value(i, j, n))
                              for j, n in enumerate(colnames))
        m.sql_execute(
            'INSERT INTO {table_name} VALUES ({table_row})'.format(**locals()))
    m.table_name = table_name
    yield m
    try:
        m.sql_execute('DROP TABLE IF EXISTS {table_name}'.format(**locals()))
    except Exception as msg:
        print('%s in deardown' % (type(msg)))


binary_fns = [
    ('add', 'int32[](int32[], int32[])', 'i4'),
    ('subtract', 'double[](double[], double[])', 'f8'),
    ('multiply', 'double[](double[], double[])', 'f8'),
    ('divide', 'double[](double[], double[])', 'f8'),
    ('logaddexp', 'double[](double[], double[])', 'f8'),
    ('logaddexp2', 'double[](double[], double[])', 'f8'),
    ('true_divide', 'double[](double[], double[])', 'f8'),
    ('floor_divide', 'double[](double[], double[])', 'f8'),
    ('power', 'float[](float[], float[])', 'f4'),
    ('remainder', 'double[](double[], double[])', 'f8'),
    ('mod', 'double[](double[], double[])', 'f8'),
    ('fmod', 'double[](double[], double[])', 'f8'),
    ('gcd', 'int64[](int64[], int64[])', 'i8'),
    ('lcm', 'int64[](int64[], int64[])', 'i8'),
    ('arctan2', 'double[](double[], double[])', 'f8'),
    ('hypot', 'double[](double[], double[])', 'f8'),
    ('maximum', 'double[](double[], double[])', 'f8'),
    ('minimum', 'double[](double[], double[])', 'f8'),
    # XXX: return a bool[] should work
    ('greater', 'int8[](int64[], int64[])', 'i8'),
    ('greater_equal', 'int8[](int64[], int64[])', 'i8'),
    ('less', 'int8[](int64[], int64[])', 'i8'),
    ('less_equal', 'int8[](int64[], int64[])', 'i8'),
    ('not_equal', 'int8[](int64[], int64[])', 'i8'),
    ('equal', 'int8[](int64[], int64[])', 'i8'),
    ('logical_and', 'int8[](int64[], int64[])', 'i8'),
    ('logical_or', 'int8[](int64[], int64[])', 'i8'),
    ('logical_xor', 'int8[](int64[], int64[])', 'i8'),
]


@pytest.mark.parametrize("method, signature, column", binary_fns,
                         ids=[item[0] for item in binary_fns])
def test_omnisci_array_binary_math_fns(omnisci, method, signature, column):
    omnisci.reset()

    s = f'def np_{method}(a, b): return omni.{method}(a, b)'
    exec(s, globals())

    omnisci(signature)(eval('np_{}'.format(method)))

    query = f'select {column}, ' + \
            f'np_{method}({column}, {column})' + \
            f' from {omnisci.table_name};'

    _, result = omnisci.sql_execute(query)

    row, out = list(result)[0]
    expected = getattr(np, method)(row, row)
    if method == 'power':
        assert np.isclose(expected, out, equal_nan=True).all(), 'omni_' + method  # noqa: E501
    else:
        assert np.array_equal(expected, out), 'omni_' + method


unary_fns = [
    # math operations
    ('negative', 'int64[](int64[])', 'i8'),
    # ('positive', 'int64[](int64[])', 'i8'),
    # ('absolute', 'int64[](double[])', 'i8'),
    ('absolute', 'double[](double[])', 'f8'),
    ('fabs', 'double[](double[])', 'f8'),
    ('rint', 'double[](double[])', 'f8'),
    # ('absolute'), 'double[](double[])', 'f8'),
    # ('conj'), 'double[](double[])', 'f8'),
    # ('conjugate'), 'double[](double[])', 'f8'),
    ('exp', 'double[](double[])', 'f8'),
    ('exp2', 'double[](double[])', 'f8'),
    ('log', 'double[](double[])', 'f8'),
    ('log2', 'double[](double[])', 'f8'),
    ('log10', 'double[](double[])', 'f8'),
    ('expm1', 'double[](double[])', 'f8'),
    ('log1p', 'double[](double[])', 'f8'),
    ('sqrt', 'double[](double[])', 'f8'),
    ('square', 'double[](double[])', 'f8'),
    # ('cbrt', 'double[](double[])', 'f8'),
    ('reciprocal', 'double[](double[])', 'f8'),
    # trigonometric functions
    ('sin', 'double[](double[])', 'f8'),
    ('cos', 'double[](double[])', 'f8'),
    ('tan', 'double[](double[])', 'f8'),
    ('arcsin', 'double[](double[])', 'f8'),
    ('arccos', 'double[](double[])', 'f8'),
    ('arctan', 'double[](double[])', 'f8'),
    ('sinh', 'double[](double[])', 'f8'),
    ('cosh', 'double[](double[])', 'f8'),
    ('tanh', 'double[](double[])', 'f8'),
    ('arcsinh', 'double[](double[])', 'f8'),
    ('arccosh', 'double[](double[])', 'f8'),
    ('arctanh', 'double[](double[])', 'f8'),
    ('deg2rad', 'double[](double[])', 'f8'),
    ('rad2deg', 'double[](double[])', 'f8'),
    # comparison functions
    ('logical_not', 'int8[](int64[])', 'i8'),
    # floating functions
    ('isfinite', 'int8[](int64[])', 'i8'),
    ('isinf', 'int8[](int64[])', 'i8'),
    ('isnan', 'int8[](int64[])', 'i8'),
    # ('isnat', 'int8[](int64[])', 'i8'), # doesn't work
    ('fabs', 'double[](double[])', 'f8'),
    # ('signbit', 'int8[](int64[])', 'i8'), # not supported
    # ('spacing', 'double[](double[])', 'f8'), # not supported
    ('floor', 'double[](double[])', 'f8'),
    ('ceil', 'double[](double[])', 'f8'),
    ('trunc', 'double[](double[])', 'f8'),
]


@pytest.mark.parametrize("method, signature, column", unary_fns,
                         ids=[item[0] for item in unary_fns])
def test_omnisci_array_unary_math_fns(omnisci, method, signature, column):
    omnisci.reset()

    s = f'def np_{method}(a): return omni.{method}(a)'
    exec(s, globals())

    omnisci(signature)(eval('np_{}'.format(method)))

    query = f'select {column}, ' + \
            f'np_{method}({column})' + \
            f' from {omnisci.table_name};'

    _, result = omnisci.sql_execute(query)

    row, out = list(result)[0]
    expected = getattr(np, method)(row)
    _isclose = ['log', 'log2', 'log10', 'log1p', 'sqrt',
                'arcsin', 'arccos', 'arccosh', 'arctanh']
    if method in _isclose:
        assert np.isclose(expected, out, equal_nan=True).all(), 'omni_' + method  # noqa: E501
    else:
        assert np.array_equal(expected, out), 'omni_' + method


def test_heaviside(omnisci):

    @omnisci('double[](int64[], int64)')
    def heaviside(x1, x2):
        return omni.heaviside(x1, x2)

    query = f'select i8, heaviside(i8, 1) from {omnisci.table_name}'
    _, result = omnisci.sql_execute(query)
    result = list(result)

    for inp, out in result:
        expected = np.heaviside(inp, 1)
        np.array_equal(expected, out)
