"""multimodel statistics.

Functions for multi-model operations
supports a multitude of multimodel statistics
computations; the only requisite is the ingested
cubes have (TIME-LAT-LON) or (TIME-PLEV-LAT-LON)
dimensions; and obviously consistent units.

It operates on different (time) spans:
- full: computes stats on full dataset time;
- overlap: computes common time overlap between datasets;

"""

import itertools
import logging
import re
from collections import defaultdict
from datetime import datetime
from functools import partial, reduce

import cf_units
import iris
import numpy as np
import scipy

logger = logging.getLogger(__name__)


def _plev_fix(dataset, pl_idx):
    """Extract valid plev data.

    this function takes care of situations
    in which certain plevs are completely
    masked due to unavailable interpolation
    boundaries.
    """
    if np.ma.is_masked(dataset):
        # keep only the valid plevs
        if not np.all(dataset.mask[pl_idx]):
            statj = np.ma.array(dataset[pl_idx], mask=dataset.mask[pl_idx])
        else:
            logger.debug('All vals in plev are masked, ignoring.')
            statj = None
    else:
        mask = np.zeros_like(dataset[pl_idx], bool)
        statj = np.ma.array(dataset[pl_idx], mask=mask)

    return statj


def _quantile(data, axis, quantile):
    """Calculate quantile.

    Workaround for calling scipy's mquantiles with arrays of >2 dimensions
    Similar to iris' _percentiles function, see their discussion:
    https://github.com/SciTools/iris/pull/625
    """
    # Ensure that the target axis is the last dimension.
    data = np.rollaxis(data, axis, start=data.ndim)
    shape = data.shape[:-1]
    # Flatten any leading dimensions.
    if shape:
        data = data.reshape([np.prod(shape), data.shape[-1]])
    # Perform the quantile calculation.
    result = scipy.stats.mstats.mquantiles(data,
                                           quantile,
                                           axis=-1,
                                           alphap=1,
                                           betap=1)
    # Ensure to unflatten any leading dimensions.
    if shape:
        result = result.reshape(shape)
    # Check whether to reduce to a scalar result
    if result.shape == (1, ):
        result = result[0]

    return result


def _compute_statistic(data, statistic_name):
    """Compute multimodel statistic."""
    data = np.ma.array(data)
    statistic = data[0]

    if statistic_name == 'median':
        statistic_function = np.ma.median
    elif statistic_name == 'mean':
        statistic_function = np.ma.mean
    elif statistic_name == 'std':
        statistic_function = np.ma.std
    elif statistic_name == 'max':
        statistic_function = np.ma.max
    elif statistic_name == 'min':
        statistic_function = np.ma.min
    elif re.match(r"^(p\d{1,2})(\.\d*)?$", statistic_name):
        # percentiles between p0 and p99.99999...
        quantile = float(statistic_name[1:]) / 100
        statistic_function = partial(_quantile, quantile=quantile)
    else:
        raise ValueError(f'No such statistic: `{statistic_name}`')

    # no plevs
    if len(data[0].shape) < 3:
        # get all NOT fully masked data - u_data
        # data is per time point
        # so we can safely NOT compute stats for single points
        if data.ndim == 1:
            u_datas = data
        else:
            u_datas = [d for d in data if not np.all(d.mask)]
        if len(u_datas) > 1:
            statistic = statistic_function(data, axis=0)
        else:
            statistic.mask = True
        return statistic

    # plevs
    for j in range(statistic.shape[0]):
        plev_check = []
        for cdata in data:
            fixed_data = _plev_fix(cdata, j)
            if fixed_data is not None:
                plev_check.append(fixed_data)

        # check for nr datasets
        if len(plev_check) > 1:
            plev_check = np.ma.array(plev_check)
            statistic[j] = statistic_function(plev_check, axis=0)
        else:
            statistic.mask[j] = True

    return statistic


def _put_in_cube(template_cube, cube_data, statistic, t_axis):
    """Quick cube building and saving."""
    tunits = template_cube.coord('time').units
    times = iris.coords.DimCoord(t_axis, standard_name='time', units=tunits)

    coord_names = [c.long_name for c in template_cube.coords()]
    coord_names.extend([c.standard_name for c in template_cube.coords()])
    if 'latitude' in coord_names:
        lats = template_cube.coord('latitude')
    else:
        lats = None
    if 'longitude' in coord_names:
        lons = template_cube.coord('longitude')
    else:
        lons = None

    # no plevs
    if len(template_cube.shape) == 3:
        cspec = [(times, 0), (lats, 1), (lons, 2)]
    # plevs
    elif len(template_cube.shape) == 4:
        plev = template_cube.coord('air_pressure')
        cspec = [(times, 0), (plev, 1), (lats, 2), (lons, 3)]
    elif len(template_cube.shape) == 1:
        cspec = [
            (times, 0),
        ]
    elif len(template_cube.shape) == 2:
        # If you're going to hardwire air_pressure into this,
        # might as well have depth here too.
        plev = template_cube.coord('depth')
        cspec = [
            (times, 0),
            (plev, 1),
        ]

    # correct dspec if necessary
    fixed_dspec = np.ma.fix_invalid(cube_data, copy=False, fill_value=1e+20)
    # put in cube
    stats_cube = iris.cube.Cube(fixed_dspec,
                                dim_coords_and_dims=cspec,
                                long_name=statistic)
    coord_names = [coord.name() for coord in template_cube.coords()]
    if 'air_pressure' in coord_names:
        if len(template_cube.shape) == 3:
            stats_cube.add_aux_coord(template_cube.coord('air_pressure'))

    stats_cube.var_name = template_cube.var_name
    stats_cube.long_name = template_cube.long_name
    stats_cube.standard_name = template_cube.standard_name
    stats_cube.units = template_cube.units
    return stats_cube


def _get_consistent_time_unit(cubes):
    """Return cubes' time unit if consistent, standard calendar otherwise."""
    t_units = [cube.coord('time').units for cube in cubes]
    if len(set(t_units)) == 1:
        return t_units[0]
    return cf_units.Unit("days since 1850-01-01", calendar="standard")


def _unify_time_coordinates(cubes):
    """
    Make sure all cubes' share the same time coordinate.

    This function extracts the date information from the cube and
    reconstructs the time coordinate, resetting the actual dates to the
    15th of the month or 1st of july for yearly data (consistent with
    `regrid_time`), so that there are no mismatches in the time arrays.

    If cubes have different time units, it will use reset the calendar to
    a default gregorian calendar with unit "days since 1850-01-01".

    Might not work for (sub)daily data, because different calendars may have
    different number of days in the year.
    """
    t_unit = _get_consistent_time_unit(cubes)

    for cube in cubes:
        # Extract date info from cube
        coord = cube.coord('time')
        years = [p.year for p in coord.units.num2date(coord.points)]
        months = [p.year for p in coord.units.num2date(coord.points)]

        # Reconstruct default calendar
        if 0 not in np.diff(years):
            # yearly data
            dates = [datetime(year, 7, 1) for year in years]

        elif 0 not in np.diff(months):
            # monthly data
            dates = [
                datetime(year, month, 15)
                for year, month in zip(years, months)
            ]
        else:
            # (sub)daily data
            coord = cube.coord('time')
            if coord.units != t_unit:
                logger.warning(
                    "Multimodel encountered (sub)daily data and inconsistent "
                    "time units or calendars. Attempting to continue, but "
                    "might produce unexpected results.")
            dates = coord.units.num2date(coord.points)

        # Update the cubes' time coordinate (both point values and the units!)
        cube.coord('time').points = t_unit.date2num(dates)
        cube.coord('time').units = t_unit
        cube.coord('time').bounds = None
        cube.coord('time').guess_bounds()


def _get_time_slice(cubes, time):
    """Fill time slice array with cubes' data if time in cube, else mask."""
    time_slice = []
    for cube in cubes:
        cube_time = cube.coord('time').points
        if time in cube_time:
            idx = int(np.argwhere(cube_time == time))
            subset = cube.data[idx]
        else:
            subset = np.ma.empty(list(cube.shape[1:]))
            subset.mask = True
        time_slice.append(subset)
    return time_slice


def _assemble_data(cubes, statistic, span='overlap'):
    """Get statistical data in iris cubes."""
    # New time array representing the union or intersection of all cubes
    time_spans = [cube.coord('time').points for cube in cubes]
    if span == 'overlap':
        new_times = reduce(np.intersect1d, time_spans)
    elif span == 'full':
        new_times = reduce(np.union1d, time_spans)
    n_times = len(new_times)

    # Target array to populate with computed statistics
    new_shape = [n_times] + list(cubes[0].shape[1:])
    stats_data = np.ma.zeros(new_shape, dtype=np.dtype('float32'))

    # Realize all cubes at once instead of separately for each time slice
    _ = [cube.data for cube in cubes]

    # Make time slices and compute stats
    for i, time in enumerate(new_times):
        time_data = _get_time_slice(cubes, time)
        stats_data[i] = _compute_statistic(time_data, statistic)

    template = cubes[0]
    stats_cube = _put_in_cube(template, stats_data, statistic, new_times)
    return stats_cube


def _multicube_statistics(cubes, statistics, span):
    """
    Compute multi-model statistics.

    Multimodel statistics computed along the time axis. Can be
    computed across a common overlap in time (set span: overlap)
    or across the full length in time of each model (set span: full).
    Restrictive computation is also available by excluding any set of
    models that the user will not want to include in the statistics
    (set exclude: [excluded models list]).

    Restrictions needed by the input data:
    - model datasets must have consistent shapes,
    - higher dimensional data is not supported (ie dims higher than four:
    time, vertical axis, two horizontal axes).

    Parameters
    ----------
    cubes: list of cubes
        list of cubes to be used in multimodel stat computation;
    span: str
        overlap or full; if overlap, statitsticss are computed on common time-
        span; if full, statistics are computed on full time spans, ignoring
        missing data.
    statistics: str
        statistical measure to be computed. Available options: mean, median,
        max, min, std, or pXX.YY (for percentile XX.YY; decimal part optional).

    Returns
    -------
    list
        list of data products or cubes containing the multimodel stats
        computed.

    Raises
    ------
    ValueError
        If span is neither overlap nor full.
    """
    logger.debug('Multimodel statistics: computing: %s', statistics)
    if len(cubes) < 2:
        logger.warning("Single dataset in list: will not compute statistics.")
        return cubes

    # Reset time coordinates and make cubes share the same calendar
    _unify_time_coordinates(cubes)

    if span == 'overlap':
        # check if we have any time overlap
        times = [cube.coord('time').points for cube in cubes]
        overlap = reduce(np.intersect1d, times)
        if len(overlap) <= 1:
            logger.info("Time overlap between cubes is none or a single point."
                        "check datasets: will not compute statistics.")
            return cubes
        logger.debug("Using common time overlap between "
                     "datasets to compute statistics.")
    elif span == 'full':
        logger.debug("Using full time spans to compute statistics.")
    else:
        raise ValueError(
            "Unexpected value for span {}, choose from 'overlap', 'full'".
            format(span))

    statistics_cubes = {}
    for statistic in statistics:
        # Compute statistic
        statistic_cube = _assemble_data(cubes, statistic, span)
        statistics_cubes[statistic] = statistic_cube

    return statistics_cubes


def _multicube_statistics_iris(cubes, statistics: list):
    """Use iris merge/collapsed to perform the aggregation.

    Equivalent to _multicube_statistics, but uses iris functions
    to perform the aggregation. This only works if the input
    cubes are very homogeneous, e.g. for different ensemble members
    of the same model/dataset.
    """
    from iris.experimental.equalise_cubes import equalise_attributes
    operators = vars(iris.analysis)

    for i, cube in enumerate(cubes):
        concat_dim = iris.coords.AuxCoord(i, var_name='ens')
        cube.add_aux_coord(concat_dim)

    equalise_attributes(cubes)

    cubes = iris.cube.CubeList(cubes)
    cube = cubes.merge_cube()

    statistics_cubes = {}
    for statistic in statistics:
        try:
            operator = operators.get(statistic.upper())
        except KeyError:
            logger.error(
                'Statistic %s not supported in ensemble_statistics. '
                'Choose supported operator from iris.analysis package.',
                statistic)

        statistic_cube = cube.collapsed('ens', operator)
        statistics_cubes[statistic] = statistic_cube

    return statistics_cubes


def _multiproduct_statistics(products,
                             statistics,
                             output_products,
                             span=None,
                             use_iris=False):
    """Compute statistics on (grouped) products, using iris or esmvalcore functions."""
    if use_iris:
        aggregator = _multicube_statistics_iris
    else:
        aggregator = partial(_multicube_statistics, span=span)

    # Extract cubes from products and compute statistics
    cubes = [cube for product in products for cube in product.cubes]
    statistics_cubes = aggregator(cubes=cubes, statistics=statistics)

    # Add statistics to output_products
    statistics_products = set()
    for statistic, cube in statistics_cubes.items():
        # Add to output product and log provenance
        statistics_product = output_products[statistic]
        statistics_product.cubes = [cube]
        for product in products:
            statistics_product.wasderivedfrom(product)
        logger.info("Generated %s", statistics_product)
        statistics_products.add(statistics_product)

    return statistics_products


def _group(products, groupby=None):
    """Group products."""
    grouped_products = defaultdict(set)
    for product in products:
        identifier = product.group(groupby)

        grouped_products[identifier].add(product)

    return grouped_products


def _grouped_multiproduct_statistics(products,
                                     statistics: list,
                                     output_products,
                                     groupby,
                                     span=None,
                                     use_iris=False):
    """Apply _multiproduct_statistics on grouped products."""
    if groupby is None:
        grouped_products = {'multi_model': products}
    else:
        grouped_products = _group(products, groupby=groupby)

    statistics_products = set()
    for identifier, products in grouped_products.items():
        sub_output_products = output_products[identifier]

        statistics_product = _multiproduct_statistics(
            products=products,
            statistics=statistics,
            output_products=sub_output_products,
            span=span,
            use_iris=use_iris,
        )

        statistics_products |= statistics_product

    return statistics_products


def multi_model_statistics(products,
                           statistics,
                           output_products,
                           span,
                           groupby=None):
    return _grouped_multiproduct_statistics(
        products=products,
        statistics=statistics,
        output_products=output_products,
        groupby=groupby,
        span=span,
    )


def ensemble_statistics(products, statistics, output_products, groupby=None):
    ensemble_grouping = ['project', 'dataset', 'exp']
    return _grouped_multiproduct_statistics(
        products=products,
        statistics=statistics,
        output_products=output_products,
        groupby=ensemble_grouping,
        use_iris=True,
    )
