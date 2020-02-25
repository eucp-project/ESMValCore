"""Module for checking iris cubes against their CMOR definitions."""
import logging
from enum import IntEnum

import cf_units
import iris.coord_categorisation
import iris.coords
import iris.exceptions
import iris.util
import numpy as np

from .table import CMOR_TABLES

CheckLevels = IntEnum(
    'CheckLevels', 'DEBUG STRICT DEFAULT RELAXED IGNORE')
"""Level of strictness of the checks.

   Values
   ------
   - DEBUG: Report any debug message that the checker wants to communicate.
   - STRICT: Fail if there are warnings regarding compliance of CMOR standards.
   - DEFAULT: Fail if cubes present any discrepancy with CMOR standards.
   - RELAXED: Fail if cubes present severe discrepancies with CMOR standards.
   - IGNORE: Do not fail for any discrepancy with CMOR standards.
"""


class CMORCheckError(Exception):
    """Exception raised when a cube does not pass the CMORCheck."""


class CMORCheck():
    """Class used to check the CMOR-compliance of the data.

    It can also fix some minor errors and does some minor data
    homogeneization:

    Parameters
    ----------
    cube: iris.cube.Cube:
        Iris cube to check.
    var_info: variables_info.VariableInfo
        Variable info to check.
    frequency: str
        Expected frequency for the data.
    fail_on_error: bool
        If true, CMORCheck stops on the first error. If false, it collects
        all possible errors before stopping.
    automatic_fixes: bool
        If True, CMORCheck will try to apply automatic fixes for any
        detected error, if possible.
    check_level: enum.IntEnum
        Level of strictness of the checks.

    Attributes
    ----------
    frequency: str
        Expected frequency for the data.
    """

    _attr_msg = '{}: {} should be {}, not {}'
    _does_msg = '{}: does not {}'
    _is_msg = '{}: is not {}'
    _vals_msg = '{}: has values {} {}'
    _contain_msg = '{}: does not contain {} {}'

    def __init__(self,
                 cube,
                 var_info,
                 frequency=None,
                 fail_on_error=False,
                 check_level=CheckLevels.DEFAULT,
                 automatic_fixes=False):

        self._cube = cube
        self._failerr = fail_on_error
        self._check_level = check_level
        self._logger = logging.getLogger(__name__)
        self._errors = list()
        self._warnings = list()
        self._debug_messages = list()
        self._cmor_var = var_info
        if not frequency:
            frequency = self._cmor_var.frequency
        self.frequency = frequency
        self.automatic_fixes = automatic_fixes

    def check_metadata(self, logger=None):
        """
        Check the cube metadata.

        Perform all the tests that do not require to have the data in memory.

        It will also report some warnings in case of minor errors and
        homogenize some data:

            - Equivalent calendars will all default to the same name.
            - Time units will be set to days since 1850-01-01


        Parameters
        ----------
        logger

        Raises
        ------
        CMORCheckError
            If errors are found. If fail_on_error attribute is set to True,
            raises as soon as an error is detected. If set to False, it perform
            all checks and then raises.

        """
        if logger is not None:
            self._logger = logger

        self._check_var_metadata()
        self._check_fill_value()
        self._check_dim_names()
        self._check_coords()
        if self.frequency != 'fx':
            self._check_time_coord()

        self._check_rank()

        self.report_debug_messages()
        self.report_warnings()
        self.report_errors()

        return self._cube

    def check_data(self, logger=None):
        """Check the cube data.

        Performs all the tests that require to have the data in memory.
        Assumes that metadata is correct, so you must call check_metadata prior
        to this.

        It will also report some warnings in case of minor errors.

        Parameters
        ----------
        logger

        Raises
        ------
        CMORCheckError
            If errors are found. If fail_on_error attribute is set to True,
            raises as soon as an error is detected. If set to False, it perform
            all checks and then raises.

        """
        if logger is not None:
            self._logger = logger

        if self._cmor_var.units:
            units = self._get_effective_units()
            if str(self._cube.units) != units:
                self._cube.convert_units(units)

        self._check_coords_data()

        self.report_warnings()
        self.report_errors()
        return self._cube

    def report_errors(self):
        """Report detected errors.

        Raises
        ------
        CMORCheckError
            If any errors were reported before calling this method.

        """
        if self.has_errors():
            msg = 'There were errors in variable {}:\n{}\nin cube:\n{}'
            msg = msg.format(self._cube.var_name, '\n '.join(self._errors),
                             self._cube)
            raise CMORCheckError(msg)

    def report_warnings(self):
        """Report detected warnings to the given logger.

        Parameters
        ----------
        logger

        """
        if self.has_warnings():
            msg = 'There were warnings in variable {}:\n{}\n'.format(
                self._cube.var_name, '\n '.join(self._warnings))
            self._logger.warning(msg)

    def report_debug_messages(self):
        """Report detected debug messages to the given logger.

        Parameters
        ----------
        logger

        """
        if self.has_debug_messages():
            msg = 'There were metadata changes in variable {}:\n{}\n'.format(
                self._cube.var_name, '\n '.join(self._debug_messages))
            self._logger.debug(msg)

    def _check_fill_value(self):
        """Check fill value."""
        # Iris removes _FillValue/missing_value information if data has none
        #  of these values. If there are values == _FillValue then it will
        #  be encoded in the numpy.ma object created.
        #
        #  => Very difficult to check!

    def _check_var_metadata(self):
        """Check metadata of variable."""
        # Check standard_name
        if self._cmor_var.standard_name:
            if self._cube.standard_name != self._cmor_var.standard_name:
                if self.automatic_fixes:
                    self.report_warning(
                        'Standard name for {} changed from {} to {}',
                        self._cube.var_name,
                        self._cube.standard_name,
                        self._cmor_var.standard_name
                    )
                    self._cube.standard_name = self._cmor_var.standard_name
                else:
                    self.report_error(
                        self._attr_msg, self._cube.var_name, 'standard_name',
                        self._cmor_var.standard_name, self._cube.standard_name
                    )
        # Check long_name
        if self._cmor_var.long_name:
            if self._cube.long_name != self._cmor_var.long_name:
                if self.automatic_fixes:
                    self.report_warning(
                        'Long name for {} changed from {} to {}',
                        self._cube.var_name,
                        self._cube.long_name,
                        self._cmor_var.long_name
                    )
                    self._cube.long_name = self._cmor_var.long_name
                else:
                    self.report_error(
                        self._attr_msg, self._cube.var_name, 'long_name',
                        self._cmor_var.long_name, self._cube.long_name
                    )

        # Check units
        if (self.automatic_fixes and self._cube.attributes.get(
                'invalid_units', '').lower() == 'psu'):
            self._cube.units = '1.0'
            del self._cube.attributes['invalid_units']

        if self._cmor_var.units:
            units = self._get_effective_units()
            if self._cube.units != units:
                if not self._cube.units.is_convertible(units):
                    self.report_error(
                        f'Variable {self._cube.var_name} units '
                        f'{self._cube.units} can not be '
                        f'converted to {self._cmor_var.units}')
                else:
                    self.report_warning(
                        f'Variable {self._cube.var_name} units '
                        f'{self._cube.units} will be '
                        f'converted to {self._cmor_var.units}')

        # Check other variable attributes that match entries in cube.attributes
        attrs = ('positive', )
        for attr in attrs:
            attr_value = getattr(self._cmor_var, attr)
            if attr_value:
                if attr not in self._cube.attributes:
                    self.report_warning('{}: attribute {} not present',
                                        self._cube.var_name, attr)
                elif self._cube.attributes[attr] != attr_value:
                    self.report_error(
                        self._attr_msg, self._cube.var_name,
                        attr, attr_value,
                        self._cube.attributes[attr])

    def _get_effective_units(self):
        """Get effective units."""
        if self._cmor_var.units.lower() == 'psu':
            units = '1.0'
        else:
            units = self._cmor_var.units
        return units

    def _check_rank(self):
        """Check rank, excluding scalar dimensions."""
        rank = 0
        dimensions = []
        for coordinate in self._cmor_var.coordinates.values():
            if coordinate.generic_level:
                rank += 1
            elif not coordinate.value:
                try:
                    for dim in self._cube.coord_dims(coordinate.standard_name):
                        dimensions.append(dim)
                except iris.exceptions.CoordinateNotFoundError:
                    # Error reported at other stages
                    pass
        rank += len(set(dimensions))

        # Check number of dimension coords matches rank
        if self._cube.ndim != rank:
            self.report_error(self._does_msg, self._cube.var_name,
                              'match coordinate rank')

    def _check_dim_names(self):
        """Check dimension names."""
        for (_, coordinate) in self._cmor_var.coordinates.items():
            if coordinate.generic_level:
                continue
            else:
                try:
                    cube_coord = self._cube.coord(var_name=coordinate.out_name)
                    if cube_coord.standard_name != coordinate.standard_name:
                        self.report_critical(
                            self._attr_msg,
                            coordinate.out_name,
                            'standard_name',
                            coordinate.standard_name,
                            cube_coord.standard_name,
                        )
                except iris.exceptions.CoordinateNotFoundError:
                    try:
                        coord = self._cube.coord(coordinate.standard_name)
                        if self._cmor_var.table_type in 'CMIP6' and \
                           coord.ndim > 1 and \
                           coord.standard_name in ['latitude', 'longitude']:
                            self.report_debug_message(
                                'Multidimensional {0} coordinate is not set '
                                'in CMOR standard. ESMValTool will change '
                                'the original value of  {1} to {2} to match '
                                'the one-dimensional case.',
                                coordinate.standard_name,
                                coord.var_name,
                                coordinate.out_name,
                            )
                            coord.var_name = coordinate.out_name
                        else:
                            self.report_error(
                                'Coordinate {0} has var name {1} '
                                'instead of {2}',
                                coordinate.name,
                                coord.var_name,
                                coordinate.out_name,
                            )
                    except iris.exceptions.CoordinateNotFoundError:
                        if coordinate.standard_name in ['time', 'latitude',
                                                        'longitude']:
                            self.report_critical(
                                self._does_msg, coordinate.name, 'exist')
                        else:
                            self.report_error(
                                self._does_msg, coordinate.name, 'exist')

    def _check_coords(self):
        """Check coordinates."""
        for coordinate in self._cmor_var.coordinates.values():
            # Cannot check generic_level coords as no CMOR information
            if coordinate.generic_level:
                continue
            var_name = coordinate.out_name

            # Get coordinate var_name as it exists!
            try:
                coord = self._cube.coord(var_name=var_name)
            except iris.exceptions.CoordinateNotFoundError:
                continue

            self._check_coord(coordinate, coord, var_name)

    def _check_coords_data(self):
        """Check coordinate data."""
        for coordinate in self._cmor_var.coordinates.values():
            # Cannot check generic_level coords as no CMOR information
            if coordinate.generic_level:
                continue
            var_name = coordinate.out_name

            # Get coordinate var_name as it exists!
            try:
                coord = self._cube.coord(var_name=var_name, dim_coords=True)
            except iris.exceptions.CoordinateNotFoundError:
                continue

            self._check_coord_monotonicity_and_direction(
                coordinate, coord, var_name)

    def _check_coord(self, cmor, coord, var_name):
        """Check single coordinate."""
        if coord.var_name == 'time':
            return
        if cmor.units:
            if str(coord.units) != cmor.units:
                fixed = False
                if self.automatic_fixes:
                    try:
                        old_unit = coord.units
                        new_unit = cf_units.Unit(cmor.units,
                                                 coord.units.calendar)
                        coord.convert_units(new_unit)
                        fixed = True
                        self.report_warning(
                            f'Coordinate {coord.var_name} units '
                            f'{str(old_unit)} '
                            f'converted to {cmor.units}')
                    except ValueError:
                        pass
                if not fixed:
                    self.report_critical(self._attr_msg, var_name, 'units',
                                         cmor.units, coord.units)
        self._check_coord_values(cmor, coord, var_name)
        self._check_coord_bounds(cmor, coord, var_name)
        self._check_coord_monotonicity_and_direction(cmor, coord, var_name)

    def _check_coord_bounds(self, cmor, coord, var_name):
        if cmor.must_have_bounds == 'yes' and not coord.has_bounds():
            if self.automatic_fixes:
                try:
                    coord.guess_bounds()
                except ValueError as ex:
                    self.report_warning(
                        'Can not guess bounds for coordinate {0} '
                        'from var {1}: {2}', coord.var_name, var_name, ex
                    )
                else:
                    self.report_warning(
                        'Added guessed bounds to coordinate {0} from var {1}',
                        coord.var_name, var_name
                    )
            else:
                self.report_warning(
                    'Coordinate {0} from var {1} does not have bounds',
                    coord.var_name, var_name
                )

    def _check_coord_monotonicity_and_direction(self, cmor, coord, var_name):
        """Check monotonicity and direction of coordinate."""
        if coord.ndim > 1:
            return
        if not coord.is_monotonic():
            self.report_critical(self._is_msg, var_name, 'monotonic')
        if len(coord.points) == 1:
            return
        if cmor.stored_direction:
            if cmor.stored_direction == 'increasing':
                if coord.points[0] > coord.points[1]:
                    if not self.automatic_fixes or coord.ndim > 1:
                        self.report_critical(
                            self._is_msg, var_name, 'increasing')
                    else:
                        self._reverse_coord(coord)
            elif cmor.stored_direction == 'decreasing':
                if coord.points[0] < coord.points[1]:
                    if not self.automatic_fixes or coord.ndim > 1:
                        self.report_critical(
                            self._is_msg, var_name, 'decreasing')
                    else:
                        self._reverse_coord(coord)

    def _reverse_coord(self, coord):
        """Reverse coordinate."""
        if coord.ndim == 1:
            self._cube = iris.util.reverse(self._cube,
                                           self._cube.coord_dims(coord))

    def _check_coord_values(self, coord_info, coord, var_name):
        """Check coordinate values."""
        # Check requested coordinate values exist in coord.points
        self._check_requested_values(coord, coord_info, var_name)

        l_fix_coord_value = False

        # Check coordinate value ranges
        if coord_info.valid_min:
            valid_min = float(coord_info.valid_min)
            if np.any(coord.points < valid_min):
                if coord_info.standard_name == 'longitude' and \
                        self.automatic_fixes:
                    l_fix_coord_value = True
                else:
                    self.report_critical(
                        self._vals_msg, var_name,
                        '< {} ='.format('valid_min'), valid_min)

        if coord_info.valid_max:
            valid_max = float(coord_info.valid_max)
            if np.any(coord.points > valid_max):
                if coord_info.standard_name == 'longitude' and \
                        self.automatic_fixes:
                    l_fix_coord_value = True
                else:
                    self.report_critical(
                        self._vals_msg, var_name,
                        '> {} ='.format('valid_max'), valid_max)

        if l_fix_coord_value:
            if coord.ndim == 1:
                lon_extent = iris.coords.CoordExtent(
                    coord, 0.0, 360., True, False)
                self._cube = self._cube.intersection(lon_extent)
            else:
                new_lons = coord.points.copy()
                self._set_range_in_0_360(new_lons)
                if coord.bounds is not None:
                    new_bounds = coord.bounds.copy()
                    self._set_range_in_0_360(new_bounds)
                else:
                    new_bounds = None
                new_coord = coord.copy(new_lons, new_bounds)
                dims = self._cube.coord_dims(coord)
                self._cube.remove_coord(coord)
                self._cube.add_aux_coord(new_coord, dims)

    @staticmethod
    def _set_range_in_0_360(array):
        while array.min() < 0:
            array[array < 0] += 360
        while array.max() > 360:
            array[array > 360] -= 360

    def _check_requested_values(self, coord, coord_info, var_name):
        """Check requested values."""
        if coord_info.requested:
            cmor_points = [float(val) for val in coord_info.requested]
            coord_points = list(coord.points)
            for point in cmor_points:
                if point not in coord_points:
                    self.report_warning(self._contain_msg, var_name,
                                        str(point), str(coord.units))

    def _check_time_coord(self):
        """Check time coordinate."""
        try:
            coord = self._cube.coord('time', dim_coords=True)
        except iris.exceptions.CoordinateNotFoundError:
            try:
                coord = self._cube.coord('time')
            except iris.exceptions.CoordinateNotFoundError:
                return

        var_name = coord.var_name
        if not coord.is_monotonic():
            self.report_error(
                'Time coordinate for var {} is not monotonic', var_name
            )

        if not coord.units.is_time_reference():
            self.report_critical(self._does_msg, var_name,
                                 'have time reference units')
        else:
            old_units = coord.units
            coord.convert_units(
                cf_units.Unit(
                    'days since 1850-1-1 00:00:00',
                    calendar=coord.units.calendar))
            simplified_cal = self._simplify_calendar(coord.units.calendar)
            coord.units = cf_units.Unit(coord.units.origin, simplified_cal)

            attrs = self._cube.attributes

            parent_time = 'parent_time_units'
            if parent_time in attrs:
                if attrs[parent_time] in 'no parent':
                    pass
                else:
                    try:
                        parent_units = cf_units.Unit(attrs[parent_time],
                                                     simplified_cal)
                    except ValueError:
                        self.report_warning('Attribute parent_time_units has '
                                            'a wrong format and cannot be '
                                            'read by cf_units. A fix needs to '
                                            'be added to convert properly '
                                            'attributes branch_time_in_parent '
                                            'and branch_time_in_child.')
                    else:
                        attrs[parent_time] = 'days since 1850-1-1 00:00:00'
                        branch_parent = 'branch_time_in_parent'
                        if branch_parent in attrs:
                            attrs[branch_parent] = parent_units.convert(
                                attrs[branch_parent], coord.units)
                        branch_child = 'branch_time_in_child'
                        if branch_child in attrs:
                            attrs[branch_child] = old_units.convert(
                                attrs[branch_child], coord.units)

        tol = 0.001
        intervals = {'dec': (3600, 3660), 'day': (1, 1)}
        freq = self.frequency
        if freq.lower().endswith('pt'):
            freq = freq[:-2]
        if freq in ['mon', 'mo']:
            for i in range(len(coord.points) - 1):
                first = coord.cell(i).point
                second = coord.cell(i + 1).point
                second_month = first.month + 1
                second_year = first.year
                if second_month == 13:
                    second_month = 1
                    second_year += 1
                if second_month != second.month or \
                   second_year != second.year:
                    msg = '{}: Frequency {} does not match input data'
                    self.report_error(msg, var_name, freq)
                    break
        elif freq == 'yr':
            for i in range(len(coord.points) - 1):
                first = coord.cell(i).point
                second = coord.cell(i + 1).point
                second_month = first.month + 1
                if first.year + 1 != second.year:
                    msg = '{}: Frequency {} does not match input data'
                    self.report_error(msg, var_name, freq)
                    break
        else:
            if freq in intervals:
                interval = intervals[freq]
                target_interval = (interval[0] - tol, interval[1] + tol)
            elif freq.endswith('hr'):
                frequency = freq[:-2]
                if frequency == 'sub':
                    frequency = 1.0 / 24
                    target_interval = (-tol, frequency + tol)
                else:
                    frequency = float(frequency) / 24
                    target_interval = (frequency - tol, frequency + tol)
            else:
                msg = '{}: Frequency {} not supported by checker'
                self.report_error(msg, var_name, freq)
                return
            for i in range(len(coord.points) - 1):
                interval = coord.points[i + 1] - coord.points[i]
                if (interval < target_interval[0]
                        or interval > target_interval[1]):
                    msg = '{}: Frequency {} does not match input data'
                    self.report_error(msg, var_name, freq)
                    break

        # remove time_origin from attributes
        coord.attributes.pop('time_origin', None)

    @staticmethod
    def _simplify_calendar(calendar):
        calendar_aliases = {
            'all_leap': '366_day',
            'noleap': '365_day',
            'standard': 'gregorian',
        }
        return calendar_aliases.get(calendar, calendar)

    def has_errors(self):
        """Check if there are reported errors.

        Returns
        -------
        bool:
            True if there are pending errors, False otherwise.

        """
        return len(self._errors) > 0

    def has_warnings(self):
        """Check if there are reported warnings.

        Returns
        -------
        bool:
            True if there are pending warnings, False otherwise.

        """
        return len(self._warnings) > 0

    def has_debug_messages(self):
        """Check if there are reported debug messages.

        Returns
        -------
        bool:
            True if there are pending debug messages, False otherwise.

        """
        return len(self._debug_messages) > 0

    def report(self, level, message, *args):
        """Generic method to report a message from the checker

        Parameters
        ----------
        level : CheckLevels
            Message level
        message : str
            Message to report
        args :
            String format args for the message

        Raises
        ------
        CMORCheckError
            If fail on error is set, it is thrown when registering an error
            message
        """
        msg = message.format(*args)
        if level == CheckLevels.DEBUG:
            if self._failerr:
                self._logger.debug(msg)
            else:
                self._debug_messages.append(msg)
        elif level < self._check_level:
            if self._failerr:
                self._logger.warning(msg)
            else:
                self._warnings.append(msg)
        else:
            if self._failerr:
                raise CMORCheckError(msg + '\nin cube:\n{}'.format(self._cube))
            self._errors.append(msg)

    def report_critical(self, message, *args):
        """Report an error.

        If fail_on_error is set to True, raises automatically.
        If fail_on_error is set to False, stores it for later reports.

        Parameters
        ----------
        message: str: unicode
            Message for the error.
        *args:
            arguments to format the message string.

        """
        self.report(CheckLevels.RELAXED, message, *args)

    def report_error(self, message, *args):
        """Report a normal error.

        Parameters
        ----------
        message: str: unicode
            Message for the error.
        *args:
            arguments to format the message string.

        """
        self.report(CheckLevels.DEFAULT, message, *args)

    def report_warning(self, message, *args):
        """Report a warning level error.

        Parameters
        ----------
        message: str: unicode
            Message for the warning.
        *args:
            arguments to format the message string.

        """
        self.report(CheckLevels.STRICT, message, *args)

    def report_debug_message(self, message, *args):
        """Report a debug message.

        Parameters
        ----------
        message: str: unicode
            Message for the debug logger.
        *args:
            arguments to format the message string

        """
        self.report(CheckLevels.DEBUG, message, *args)


def _get_cmor_checker(table,
                      mip,
                      short_name,
                      frequency,
                      fail_on_error=False,
                      check_level=CheckLevels.DEFAULT,
                      automatic_fixes=False):
    """Get a CMOR checker/fixer."""
    if table not in CMOR_TABLES:
        raise NotImplementedError(
            "No CMOR checker implemented for table {}."
            "\nThe following options are available: {}".format(
                table, ', '.join(CMOR_TABLES)))

    cmor_table = CMOR_TABLES[table]
    var_info = cmor_table.get_variable(mip, short_name)
    if var_info is None:
        var_info = CMOR_TABLES['custom'].get_variable(mip, short_name)

    def _checker(cube):
        return CMORCheck(
            cube,
            var_info,
            frequency=frequency,
            fail_on_error=fail_on_error,
            check_level=check_level,
            automatic_fixes=automatic_fixes)

    return _checker


def cmor_check_metadata(cube, cmor_table, mip,
                        short_name, frequency,
                        check_level):
    """Check if metadata conforms to variable's CMOR definiton.

    None of the checks at this step will force the cube to load the data.

    Parameters
    ----------
    cube: iris.cube.Cube
        Data cube to check.
    cmor_table: basestring
        CMOR definitions to use.
    mip:
        Variable's mip.
    short_name: basestring
        Variable's short name.
    frequency: basestring
        Data frequency.
    check_level: enum.IntEnum
        Level of strictness of the checks.

    """
    checker = _get_cmor_checker(cmor_table, mip,
                                short_name, frequency,
                                check_level=check_level)
    checker(cube).check_metadata()
    return cube


def cmor_check_data(cube, cmor_table, mip, short_name, frequency, check_level):
    """Check if data conforms to variable's CMOR definiton.

    The checks performed at this step require the data in memory.

    Parameters
    ----------
    cube: iris.cube.Cube
        Data cube to check.
    cmor_table: basestring
        CMOR definitions to use.
    mip:
        Variable's mip.
    short_name: basestring
        Variable's short name
    frequency: basestring
        Data frequency
    check_level: enum.IntEnum
        Level of strictness of the checks.

    """
    checker = _get_cmor_checker(cmor_table, mip, short_name, frequency,
                                check_level=check_level)
    checker(cube).check_data()
    return cube


def cmor_check(cube, cmor_table, mip, short_name, frequency, check_level):
    """Check if cube conforms to variable's CMOR definiton.

    Equivalent to calling cmor_check_metadata and cmor_check_data
    consecutively.

    Parameters
    ----------
    cube: iris.cube.Cube
        Data cube to check.
    cmor_table: basestring
        CMOR definitions to use.
    mip:
        Variable's mip.
    short_name: basestring
        Variable's short name.
    frequency: basestring
        Data frequency.
    check_level: enum.IntEnum
        Level of strictness of the checks.

    """
    cmor_check_metadata(cube, cmor_table, mip, short_name, frequency,
                        check_level=check_level)
    cmor_check_data(cube, cmor_table, mip, short_name, frequency,
                    check_level=check_level)
    return cube
