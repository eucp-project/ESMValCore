"""
Area operations on data cubes.

Allows for selecting data subsets using certain latitude and longitude bounds;
selecting geographical regions; constructing area averages; etc.
"""
import logging

import fiona
import iris
import numpy as np
import shapely
import shapely.ops
from dask import array as da

from _mask import _mask_with_shp

logger = logging.getLogger(__name__)


# guess bounds tool
def _guess_bounds(cube, coords):
    """Guess bounds of a cube, or not."""
    # check for bounds just in case
    for coord in coords:
        if not cube.coord(coord).has_bounds():
            cube.coord(coord).guess_bounds()
    return cube


# slice cube over a restricted area (box)
def extract_region(cube, start_longitude, end_longitude, start_latitude,
                   end_latitude):
    """
    Extract a region from a cube.

    Function that subsets a cube on a box (start_longitude, end_longitude,
    start_latitude, end_latitude)
    This function is a restriction of masked_cube_lonlat().

    Parameters
    ----------
    cube: iris.cube.Cube
        input data cube.
    start_longitude: float
        Western boundary longitude.
    end_longitude: float
        Eastern boundary longitude.
    start_latitude: float
        Southern Boundary latitude.
    end_latitude: float
        Northern Boundary Latitude.

    Returns
    -------
    iris.cube.Cube
        smaller cube.
    """
    # Converts Negative longitudes to 0 -> 360. standard
    start_longitude = float(start_longitude)
    end_longitude = float(end_longitude)
    start_latitude = float(start_latitude)
    end_latitude = float(end_latitude)

    if cube.coord('latitude').ndim == 1:
        region_subset = cube.intersection(longitude=(start_longitude,
                                                     end_longitude),
                                          latitude=(start_latitude,
                                                    end_latitude))
        region_subset = region_subset.intersection(longitude=(0., 360.))
        return region_subset
    # irregular grids
    lats = cube.coord('latitude').points
    lons = cube.coord('longitude').points
    select_lats = start_latitude < lats < end_latitude
    select_lons = start_longitude < lons < end_longitude
    selection = select_lats & select_lons
    data = da.ma.masked_where(~selection, cube.core_data())
    return cube.copy(data)


def _get_bbox_from_shp(shapefile):
    return


def extract_shape(cube, shapefilename, crop=True):
    """
    Extract a shapefile specified region from a cube.

    Function that subsets a cube

    Parameters
    ----------
    cube: iris.cube.Cube
        input data cube.

    shapefilename: str
        path to the shapefile.

    crop: bool, optional (default: True)
        minimize the size of the resulting cube to the bounds of the shape.
    """
    if crop:
        bbox = _get_bbox_from_shp(shapefilename)
        cube = cube.intersection(bbox)

    cube = _mask_with_shp(cube, shapefilename)
    #not sure whether we can (and should) actually use the _mask_with_shp
    #function as it is built for land/sea masks for the full world map 

    return cube


def get_iris_analysis_operation(operator):
    """
    Determine the iris analysis operator from a string.

    Map string to functional operator.

    Parameters
    ----------
    operator: str
        A named operator.

    Returns
    -------
        function: A function from iris.analysis

    Raises
    ------
    ValueError
        operator not in allowed operators list.
        allowed operators: mean, median, std_dev, variance, min, max
    """
    operators = ['mean', 'median', 'std_dev', 'variance', 'min', 'max']
    operator = operator.lower()
    if operator not in operators:
        raise ValueError("operator {} not recognised. "
                         "Accepted values are: {}."
                         "".format(operator, ', '.join(operators)))
    operation = getattr(iris.analysis, operator.upper())
    return operation


def zonal_means(cube, coordinate, mean_type):
    """
    Get zonal means.

    Function that returns zonal means along a coordinate `coordinate`;
    the type of mean is controlled by mean_type variable (string):
    - 'mean' -> MEAN
    - 'median' -> MEDIAN
    - 'std_dev' -> STD_DEV
    - 'variance' -> VARIANCE
    - 'min' -> MIN
    - 'max' -> MAX

    Parameters
    ----------
    cube: iris.cube.Cube
        input cube.
    coordinate: str
        name of coordinate to make mean.
    mean_type: str
        Type of analysis to use, from iris.analysis.

    Returns
    -------
    iris.cube.Cube
    """
    operation = get_iris_analysis_operation(mean_type)
    return cube.collapsed(coordinate, operation)


def tile_grid_areas(cube, fx_files):
    """
    Tile the grid area data to match the dataset cube.

    Parameters
    ----------
    cube: iris.cube.Cube
        input cube.
    fx_files: dict
        dictionary of field:filename for the fx_files

    Returns
    -------
    iris.cube.Cube
        Freshly tiled grid areas cube.
    """
    grid_areas = None
    if fx_files:
        for key, fx_file in fx_files.items():
            if fx_file is None:
                continue
            logger.info('Attempting to load %s from file: %s', key, fx_file)
            fx_cube = iris.load_cube(fx_file)

            grid_areas = fx_cube.core_data()
            if cube.ndim == 4 and grid_areas.ndim == 2:
                grid_areas = da.tile(grid_areas,
                                     [cube.shape[0], cube.shape[1], 1, 1])
            elif cube.ndim == 4 and grid_areas.ndim == 3:
                grid_areas = da.tile(grid_areas, [cube.shape[0], 1, 1, 1])
            elif cube.ndim == 3 and grid_areas.ndim == 2:
                grid_areas = da.tile(grid_areas, [cube.shape[0], 1, 1])
            else:
                raise ValueError('Grid and dataset number of dimensions not '
                                 'recognised: {} and {}.'
                                 ''.format(cube.ndim, grid_areas.ndim))
    return grid_areas


# get the area average
def area_statistics(cube, operator, fx_files=None):
    """
    Apply a statistical operator in the horizontal direction.

    The average in the horizontal direction. We assume that the
    horizontal directions are ['longitude', 'latutude'].

    This function can be used to apply
    several different operations in the horizonal plane: mean, standard
    deviation, median variance, minimum and maximum. These options are
    specified using the `operator` argument and the following key word
    arguments:

    +------------+--------------------------------------------------+
    | `mean`     | Area weighted mean.                              |
    +------------+--------------------------------------------------+
    | `median`   | Median (not area weighted)                       |
    +------------+--------------------------------------------------+
    | `std_dev`  | Standard Deviation (not area weighted)           |
    +------------+--------------------------------------------------+
    | `variance` | Variance (not area weighted)                     |
    +------------+--------------------------------------------------+
    | `min`:     | Minimum value                                    |
    +------------+--------------------------------------------------+
    | `max`      | Maximum value                                    |
    +------------+--------------------------------------------------+

    Parameters
    ----------
        cube: iris.cube.Cube
            Input cube.
        operator: str
            The operation, options: mean, median, min, max, std_dev, variance
        fx_files: dict
            dictionary of field:filename for the fx_files

    Returns
    -------
    iris.cube.Cube
        collapsed cube.

    Raises
    ------
    iris.exceptions.CoordinateMultiDimError
        Exception for latitude axis with dim > 2.
    ValueError
        if input data cube has different shape than grid area weights
    """
    grid_areas = tile_grid_areas(cube, fx_files)

    if not fx_files and cube.coord('latitude').points.ndim == 2:
        logger.error(
            'fx_file needed to calculate grid cell area for irregular grids.')
        raise iris.exceptions.CoordinateMultiDimError(cube.coord('latitude'))

    coord_names = ['longitude', 'latitude']
    if grid_areas is None or not grid_areas.any():
        cube = _guess_bounds(cube, coord_names)
        grid_areas = iris.analysis.cartography.area_weights(cube)
        logger.info('Calculated grid area shape: %s', grid_areas.shape)

    if cube.shape != grid_areas.shape:
        raise ValueError('Cube shape ({}) doesn`t match grid area shape '
                         '({})'.format(cube.shape, grid_areas.shape))

    operation = get_iris_analysis_operation(operator)

    # TODO: implement weighted stdev, median, s var when available in iris.
    # See iris issue: https://github.com/SciTools/iris/issues/3208

    if operator == 'mean':
        return cube.collapsed(coord_names, operation, weights=grid_areas)

    # Many IRIS analysis functions do not accept weights arguments.
    return cube.collapsed(coord_names, operation)


def extract_named_regions(cube, regions):
    """
    Extract a specific named region.

    The region coordinate exist in certain CMIP datasets.
    This preprocessor allows a specific named regions to be extracted.

    Parameters
    ----------
    cube: iris.cube.Cube
       input cube.
    regions: str, list
        A region or list of regions to extract.

    Returns
    -------
    iris.cube.Cube
        collapsed cube.

    Raises
    ------
    ValueError
        regions is not list or tuple or set.
    ValueError
        region not included in cube.
    """
    # Make sure regions is a list of strings
    if isinstance(regions, str):
        regions = [regions]

    if not isinstance(regions, (list, tuple, set)):
        raise TypeError(
            'Regions "{}" is not an acceptable format.'.format(regions))

    available_regions = set(cube.coord('region').points)
    invalid_regions = set(regions) - available_regions
    if invalid_regions:
        raise ValueError('Region(s) "{}" not in cube region(s): {}'.format(
            invalid_regions, available_regions))

    constraints = iris.Constraint(region=lambda r: r in regions)
    cube = cube.extract(constraint=constraints)
    return cube


def extract_shape(cube, shapefile, method='contains', clip=True):
    """Extract a region defined by a shapefile.

    Parameters
    ----------
    cube: iris.cube.Cube
       input cube.
    shapefile: str
        A shapefile defining the region(s) to extract.
    method: str
        Select all points contained by the shape ('contains') or
        select a single representative point ('representative').
    clip: bool
        Clip the resulting cube ('true') or not ('false').

    Returns
    -------
    iris.cube.Cube
        Cube containing the extracted region.

    """
    if method not in {'contains', 'nearest'}:
        raise ValueError(
            "Invalid value for `method`. Choose from 'containts', 'nearest'.")
    lon_coord = cube.coord(axis='X')
    lat_coord = cube.coord(axis='Y')
    with fiona.open(shapefile) as geometries:
        if clip and lon_coord.ndim == 1 and lat_coord.ndim == 1:
            (
                start_longitude,
                start_latitude,
                end_longitude,
                end_latitude,
            ) = geometries.bounds
            lon_bound = lon_coord.core_bounds()[0]
            lon_step = lon_bound[1] - lon_bound[0]
            start_longitude -= lon_step
            end_longitude += lon_step
            lat_bound = lat_coord.core_bounds()[0]
            lat_step = lat_bound[1] - lat_bound[0]
            start_latitude -= lat_step
            end_latitude += lat_step
            cube = extract_region(cube, start_longitude, end_longitude,
                                  start_latitude, end_latitude)
            lon_coord = cube.coord(axis='X')
            lat_coord = cube.coord(axis='Y')


#         mask = rasterio.features.geometry_mask(
#             geometries,
#             cube.shape[1:3],
#             transform=rasterio.features.IDENTITY,
#             all_touched=True,
#             invert=True)
        lon = lon_coord.points
        lat = lat_coord.points
        if lon_coord.ndim == 1 and lat_coord.ndim == 1:
            lon, lat = np.meshgrid(lon.flat, lat.flat, copy=False)

        selection = np.zeros(lat.shape, dtype=bool)
        for item in geometries:
            shape = shapely.geometry.shape(item['geometry'])
            if method == 'contains':
                select = shapely.vectorized.contains(shape, lon, lat)
            if method == 'nearest' or not select.any():
                representative_point = shape.representative_point()
                points = shapely.geometry.MultiPoint(
                    np.stack((lon.flat, lat.flat), axis=1))
                nearest_point = shapely.ops.nearest_points(
                    points, representative_point)[0]
                nearest_lon, nearest_lat = nearest_point.coords[0]
                select = (lon == nearest_lon) & (lat == nearest_lat)
            selection |= select

        # print('selecting', np.sum(selection), 'points')
        selection = da.broadcast_to(selection, cube.shape)
        cube.data = da.ma.masked_where(~selection, cube.core_data())
        return cube
