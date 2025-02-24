#! /usr/bin/python3
print("RUNNING PYTHON VERSION")

# libstreams is a python library that is generated by f2py build routines in
# the apptainer build pipeline. If you change anything in the fortran code, you
# must rebuild libstreams with f2py before the changes are reflected in the python 
# code
import libstreams as streams 
# we have to start MPI here before importing the mpi4py library
# otherwise, there will be an error in the streams code when they attempt to 
# initialize
streams.wrap_startmpi()

from mpi4py import MPI
import json
import math

#
# initialize some global variables
# we do this here since some modules after this will attempt to import global variables
# and if they have not been initialized it will error
#
import globals
globals.init()
from globals import rank, comm

import io_utils
import numpy as np
from config import Config
import utils

#
# Load in config, initialize
#

with open("/input/input.json", "r") as f:
    json_data = json.load(f)
    config = Config.from_json(json_data)

#
# allocate arrays so we dont need to reallocate in the solver loop
#
span_average = np.zeros([5, config.nx_mpi(), config.ny_mpi()], dtype=np.float64)
temp_field = np.zeros((config.nx_mpi(), config.ny_mpi(), config.nz_mpi()), dtype=np.float64)
dt_array = np.zeros(1)
time_array = np.zeros(1)

#
# execute streams setup routines
#

streams.wrap_setup()
streams.wrap_init_solver()

# initialize files

#
# Initialize datasets and HDF5 output files
#
flowfields = io_utils.IoFile("/distribute_save/flowfields.h5")
span_averages = io_utils.IoFile("/distribute_save/span_averages.h5")
trajectories = io_utils.IoFile("/distribute_save/trajectories.h5")
mesh_h5 = io_utils.IoFile("/distribute_save/mesh.h5")

grid_shape = [config.grid.nx, config.grid.ny, config.grid.nz]
span_average_shape = [config.grid.nx, config.grid.ny]

# 3D flowfield files
if not (config.temporal.full_flowfield_io_steps is None):
    flowfield_writes = int(math.ceil(config.temporal.num_iter / config.temporal.full_flowfield_io_steps))
else:
    flowfield_writes = 0
velocity_dset = io_utils.VectorField3D(flowfields, [5, *grid_shape], flowfield_writes, "velocity", rank)
flowfield_time_dset = io_utils.Scalar1D(flowfields, [1], flowfield_writes, "time", rank)

# span average files 
numwrites = int(math.ceil(config.temporal.num_iter / config.temporal.span_average_io_steps))
span_average_dset = io_utils.VectorFieldXY2D(span_averages, [5, *span_average_shape], numwrites, "span_average", rank)
shear_stress_dset = io_utils.ScalarFieldX1D(span_averages, [config.grid.nx], numwrites, "shear_stress", rank)
span_average_time_dset = io_utils.Scalar0D(span_averages, [1], numwrites, "time", rank)

# trajectories files
dt_dset = io_utils.Scalar0D(trajectories, [1], config.temporal.num_iter, "dt", rank)

# mesh datasets
x_mesh_dset = io_utils.Scalar1DX(mesh_h5, [config.grid.nx], 1, "x_grid", rank)
y_mesh_dset = io_utils.Scalar1D(mesh_h5, [config.grid.ny], 1, "y_grid", rank)
z_mesh_dset = io_utils.Scalar1D(mesh_h5, [config.grid.nz], 1, "z_grid", rank)

x_mesh = streams.mod_streams.x[config.x_start():config.x_end()]
y_mesh = streams.mod_streams.y[config.y_start():config.y_end()]
z_mesh = streams.mod_streams.z[config.z_start():config.z_end()]

x_mesh_dset.write_array(x_mesh)
y_mesh_dset.write_array(y_mesh)
z_mesh_dset.write_array(z_mesh)

#
# Main solver loop, we start time stepping until we are done
#

time = 0
for i in range(config.temporal.num_iter):
    streams.wrap_step_solver()

    time += streams.mod_streams.dtglobal
    time_array[:] = time

    if (i % config.temporal.span_average_io_steps) == 0:
        utils.hprint("writing span average to output")
        streams.wrap_copy_gpu_to_cpu()
        streams_data_slice = config.slice_flowfield_array(streams.mod_streams.w)
        utils.calculate_span_averages(config, span_average, temp_field, streams_data_slice)

        span_average_dset.write_array(span_average)

        # also write shear stress information
        streams.wrap_tauw_calculate()
        shear_stress_dset.write_array(streams.mod_streams.tauw_x)

        # write the time at which this data was collected
        span_average_time_dset.write_array(time_array)

    # save dt information for every step
    dt_array[:] = streams.mod_streams.dtglobal
    dt_dset.write_array(dt_array)

    if not (config.temporal.full_flowfield_io_steps is None):
        if (i % config.temporal.full_flowfield_io_steps) == 0:
            utils.hprint("writing flowfield")
            streams.wrap_copy_gpu_to_cpu()
            velocity_dset.write_array(config.slice_flowfield_array(streams.mod_streams.w))

            # write the time at which this data was collected
            flowfield_time_dset.write_array(time_array)

#
# wrap up execution of solver
#

streams.wrap_finalize_solver()

print("finalizing solver")
streams.wrap_finalize()
