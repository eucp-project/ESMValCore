# ESMValCore package
This is a copy of the ESMValCore repository taken to preserve the version of code used to produce work from the EUCP WP5 "Lines of Evidence" work package.

See https://github.com/eucp-project/Lines-of-evidence-catalog for further information.

The version of ESMValCore used is https://github.com/eucp-project/ESMValCore/releases/tag/v2.4.0-EUCP_WP5

[![Documentation Status](https://readthedocs.org/projects/esmvaltool/badge/?version=latest)](https://esmvaltool.readthedocs.io/en/latest/?badge=latest)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.3387139.svg)](https://doi.org/10.5281/zenodo.3387139)
[![Gitter](https://badges.gitter.im/Join%20Chat.svg)](https://gitter.im/ESMValGroup?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge)
[![CircleCI](https://circleci.com/gh/ESMValGroup/ESMValCore/tree/main.svg?style=svg)](https://circleci.com/gh/ESMValGroup/ESMValCore/tree/main)
[![codecov](https://codecov.io/gh/ESMValGroup/ESMValCore/branch/main/graph/badge.svg?token=wQnDzguwq6)](https://codecov.io/gh/ESMValGroup/ESMValCore)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/5d496dea9ef64ec68e448a6df5a65783)](https://www.codacy.com/gh/ESMValGroup/ESMValCore?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=ESMValGroup/ESMValCore&amp;utm_campaign=Badge_Grade)
[![Docker Build Status](https://img.shields.io/docker/cloud/build/esmvalgroup/esmvalcore)](https://hub.docker.com/r/esmvalgroup/esmvalcore/)
[![Anaconda-Server Badge](https://anaconda.org/esmvalgroup/esmvalcore/badges/installer/conda.svg)](https://conda.anaconda.org/esmvalgroup)

ESMValCore: core functionalities for the ESMValTool, a community diagnostic
and performance metrics tool for routine evaluation of Earth System Models
in the Climate Model Intercomparison Project (CMIP).

# Getting started

Please have a look at the
[documentation](https://docs.esmvaltool.org/projects/esmvalcore/en/latest/quickstart/install.html)
to get started.

## Using the ESMValCore package to run recipes

The ESMValCore package provides the `esmvaltool` command, which can be used to run
[recipes](https://docs.esmvaltool.org/projects/esmvalcore/en/latest/recipe/overview.html)
for working with CMIP-like data.
A large collection of ready to use
[recipes and diagnostics](https://docs.esmvaltool.org/en/latest/recipes/index.html)
is provided by the
[ESMValTool](https://github.com/ESMValGroup/ESMValTool)
package.

## Using ESMValCore as a Python library

The ESMValCore package provides various functions for:

-   Finding data in a directory structure typically used for CMIP data.

-   Reading CMIP/CMOR tables and using those to check model and observational data.

-   ESMValTool preprocessor functions based on
    [iris](https://scitools-iris.readthedocs.io) for e.g. regridding,
    vertical interpolation, statistics, correcting (meta)data errors, extracting
    a time range, etcetera.

read all about it in the
[API documentation](https://docs.esmvaltool.org/projects/esmvalcore/en/latest/api/esmvalcore.html).

## Getting help

The easiest way to get help if you cannot find the answer in the documentation
on [readthedocs](https://docs.esmvaltool.org), is to open an
[issue on GitHub](https://github.com/ESMValGroup/ESMValCore/issues).

## Contributing

Contributions are very welcome, please read our
[contribution guidelines](https://docs.esmvaltool.org/projects/ESMValCore/en/latest/contributing.html)
to get started.
