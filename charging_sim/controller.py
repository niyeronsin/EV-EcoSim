import os
from optimization import Optimization
from utils import build_objective, build_electricity_cost, num_steps
import numpy as np
import tensorflow as tf
# import matplotlib.pyplot as plt
from sklearn.preprocessing import MinMaxScaler
import cvxpy as cp

# Battery_state should include: Estimate SOH corrected from previous day, SOC,

# Get the load predictive models
path_prefix = os.getcwd()
path_prefix = path_prefix[0:path_prefix.index('EV50_cosimulation')] + 'EV50_cosimulation'
path_prefix.replace('\\', '/')
LSTM1 = tf.keras.models.load_model(path_prefix+'/DLMODELS/LSTM_01.h5')
LSTM2 = tf.keras.models.load_model(path_prefix+'/DLMODELS/LSTM_ONE_STEP.h5')
OCV_SOC_linear_params = np.load(path_prefix+'/BatteryData/OCV_SOC_linear_params_NMC_25degC.npy')

"""Build different MPC classes for different horizons maybe? It's hard to do one MPC for all horizons due to ML-side"""


class MPC:
    """TODO: need to code-in a deterministic scenario that does not run in an MPC fashion.
    Compare how RMSE in future load trajectory impacts the cost of station design in terms of Storage"""
    def __init__(self, config, storage=None, solar=None):
        self.config = config
        self.resolution = config["resolution"]  # should match object interval? not necessary
        self.charge_history = np.genfromtxt(path_prefix+config["load_history"]) * 1
        self.current_testdata = np.genfromtxt(path_prefix+config["simulation_load"])[:-1, ] * 1 # this is used to predict the load, in the future, we will generate a bunch of loads to do this
        self.reshaped_data = np.reshape(self.current_testdata, self.current_testdata .size) # flatten data for efficient indexing
        self.one_step_data = self.reshaped_data[-48:]  # one step LSTM uses last 48 time steps to predict the next, need
        self.std_data = np.std(self.current_testdata, 0)   # from training distribution
        self.std_data[self.std_data == 0] = 1
        self.mean_data = np.mean(self.current_testdata, 0)
        self.scaled_test_data = (self.current_testdata - self.mean_data) / self.std_data    # should use history
        self.storage = storage
        self.solar = solar
        self.storage_constraints = None
        self.load = np.array([])
        self.w = 0
        self.costs = []
        self.control_battery = self.config["control_battery"]

        # BATTERY VARIABLES (TODO: INCLUDE MIN-MAX SOC AND DISCHARGE REQUIREMENTS DIRECTLY IN CONTROLLER)
        if storage:
            # self.battery_start = self.storage.start
            self.battery_initial_SOC = self.storage.initial_SOC  # begin with initial information of batt SOC
            self.battery_capacity = self.storage.nominal_cap    # controller should be estimating this from time to time. Or decide how it is updated?
        # self.battery_power_charge = cp.Variable((num_steps, 1))
        # self.battery_power_discharge = cp.Variable((num_steps, 1))
        self.battery_power = cp.Variable((num_steps, 1))
        self.battery_current = cp.Variable((num_steps, 1))
        self.battery_OCV = cp.Variable((num_steps, 1))
        self.battery_voltage = cp.Variable((num_steps, 1))
        self.battery_Q = cp.Variable((num_steps + 1, 1))  # Amount of energy Kwh available in battery
        self.battery_SOC = cp.Variable((num_steps + 1, 1))  # State of Charge max:1 min:0

        self.scaler_onestep = MinMaxScaler()
        load_history_onestep = np.reshape(self.charge_history, (self.charge_history.size, 1))  # scaling based on historical dist
        self.scaler_onestep.fit(load_history_onestep)

        self.scaled_onestep_data = self.scaler_onestep.transform(
            np.reshape(self.current_testdata, (self.current_testdata.size, 1)))
        # self.scaler_onestep = scaled_onestep_data[1]
        self.full_day_prediction = np.array([])
        self.action = 0
        self.actions = [self.action]
        # self.LSTM_model = tf.keras.models.load_model("LSTM_01.h5")

    def initialize_forecast_data(self):
        """loads history to be used for forecasting EV charging"""
        self.charge_history = np.genfromtxt(path_prefix+self.config["load_history"]) * 1
        self.current_testdata = np.genfromtxt(path_prefix+self.config["simulation_load"])[:-1, ] * 1

    def compute_control(self, start, shift, stop, price_vector):
        """This should never be run for centralized battery storage simulation"""
        predicted_load = self.predict_load(start, shift, stop)
        control_action = None
        if self.control_battery:
            # battery_constraints = self.get_battery_constraints(predicted_load)  # battery constraints
            objective_mode = "Electricity Cost"  # Need to update objective modes to include cost function design
            linear_aging_cost = 0  # based on simple model and predicted control actions - Change this to zero
            electricity_cost = build_electricity_cost(self, predicted_load, price_vector)  # based on prediction as well
            objective = build_objective(objective_mode, electricity_cost, linear_aging_cost)
            opt_problem = Optimization(objective_mode, objective, self, predicted_load, self.resolution, None,
                                       self.storage, time=0, name="Test_Case_" + str(self.storage.id))
            cost = opt_problem.run()
            self.costs.append(cost)
            # print("Optimal cost is: ", sum(self.costs)/len(self.costs))
            if opt_problem.problem.status != 'optimal':
                print('Unable to service travel')
            if electricity_cost.value < 0:
                print('Negative Electricity Cost')
            # change to current
            control_action = self.battery_current.value[0, 0]   # this is current flowing through each cell
            # print("checking powers", self.battery_power_charge.value[0], self.battery_power_discharge.value[0])
            self.actions.append(control_action)
            self.storage.update_capacity()  # to track linear estimated aging
            # obtain the true state of charge from the batteryAgingSim (How frequently though?)
            if len(self.storage.control_current) < num_steps:
                self.storage.control_current = np.append(self.storage.control_current, control_action)
            else:
                self.storage.control_current = np.array([control_action])  # it should be only updating one but then
                # print("RESETTING CONTROL ARRAY", len(self.storage.control_current))
                # I am showing results after each step I think.
                # Need to also fix the compute current scheme currently being used

        #  need to get all the states here after the first action is taken
        return control_action, predicted_load

    def predict_load(self, start, shift, stop, days_length=14):
        # print("start, stop, shift", start, stop, shift)
        """this uses two ML models for predictions. One for full day prediction (runs only once a day) and the
        other for time-step update"""
        begin = stop * 96 - 48 + shift  # shift at each time-step, then reset after a day is done
        end = begin + 48
        test_input_onestep = np.reshape(self.scaled_onestep_data[begin:end], (1, 48, 1))
        if not self.full_day_prediction.any():   # This checks if a full day is done
            test_input_fullday = np.reshape(self.reshaped_data[start:start + days_length * num_steps],
                                            (1, days_length, num_steps))
            self.full_day_prediction = LSTM1.predict(test_input_fullday) * self.std_data + self.mean_data
            self.full_day_prediction.shape = (num_steps, 1)
        prediction_next_step = self.scaler_onestep.inverse_transform(LSTM2.predict(test_input_onestep))
        index = len(self.load) + 1  # this is tracking what time step we are at
        prediction = np.append(self.load, prediction_next_step)     # include previous day's known load
        prediction = np.append(prediction, self.full_day_prediction[index:, :])
        prediction = np.reshape(prediction, (96, 1))
        return prediction

    def get_battery_constraints(self, EV_load):
        eps = 0.0001  # This is a numerical artifact. Values tend to solve at very low negative values but
        # this helps avoid it.
        # TODO: INCLUDE SOLAR
        num_cells_series = self.storage.topology[0]
        num_modules_parallel = self.storage.topology[1]     # maybe make this easier later?? abstract it out
        num_cells = self.storage.topology[2]
        self.storage_constraints = [self.battery_SOC[0] == self.battery_initial_SOC,
                            self.battery_OCV == OCV_SOC_linear_params[0] * self.battery_SOC[0:num_steps] + OCV_SOC_linear_params[1],
                            cp.abs(self.battery_current) <= self.storage.max_current,
                            self.battery_voltage == self.battery_OCV + cp.multiply(self.battery_current, 0.076),
                            self.battery_power == cp.multiply(self.storage.nominal_pack_voltage,
                                                      self.battery_current * num_modules_parallel) / 1000,  # kw
                            # self.battery_power == self.battery_power_charge + self.battery_power_discharge,
                            self.battery_SOC[1:num_steps + 1] == self.battery_SOC[0:num_steps] \
                                    + (self.resolution / 60 * self.battery_current) / self.storage.cap, # removed self-discharge
                            # self.battery_power_discharge <= 0,
                            # self.battery_power_charge >= 0,
                            self.battery_SOC >= self.storage.min_SOC,
                            self.battery_SOC <= self.storage.max_SOC,
                            EV_load + self.battery_power - 0 >= eps
                            # no injecting back to the grid; should try unconstrained. This could be infeasible.
                            ]
        if self.solar:
            self.storage_constraints.extend([self.solar.get_constraints()])
        return self.storage_constraints

    def reset_load(self):
        """This is done after one full day is done."""
        self.load = np.array([])
        self.full_day_prediction = np.array([])


class MPCBatt:
    """This is for controlling the battery ONLY at centralized location"""
    def __init__(self, config, storage):
        self.config = config
        self.resolution = config["resolution"]
        self.storage = storage
        self.storage_constraints = None
        self.control_battery = self.config["control_battery"]

        # BATTERY VARIABLES (TODO: INCLUDE MIN-MAX SOC AND DISCHARGE REQUIREMENTS DIRECTLY IN CONTROLLER)
        # self.battery_start = self.storage.start
        self.battery_initial_SOC = self.storage.initial_SOC  # begin with initial information of batt SOC
        self.battery_capacity = self.storage.nominal_cap    # controller should be estimating this from time to time. Or decide how it is updated?
        self.battery_power_charge = cp.Variable((num_steps, 1))
        self.battery_power_discharge = cp.Variable((num_steps, 1))

        self.battery_power_discharge = cp.Variable((num_steps, 1))
        self.battery_power = cp.Variable((num_steps, 1))
        self.battery_current = cp.Variable((num_steps, 1))
        self.battery_OCV = cp.Variable((num_steps, 1))
        self.battery_voltage = cp.Variable((num_steps, 1))
        self.battery_Q = cp.Variable((num_steps + 1, 1))  # Amount of energy Kwh available in battery
        self.battery_SOC = cp.Variable((num_steps + 1, 1))  # State of Charge max:1 min:0

        self.actions = []
        # self.LSTM_model = tf.keras.models.load_model("LSTM_01.h5")

    def initialize_forecast_data(self):
        """loads history to be used for forecasting EV charging"""
        self.charge_history = np.genfromtxt(path_prefix+self.config["load_history"])
        self.current_testdata = np.genfromtxt(path_prefix+self.config["simulation_load"])[:-1, ] / 1

    def compute_control(self, price_vector, predicted_load):
        # add indicator if perfect foresight or not...if perfect foresight, we do not actually need controller
        # so instead we can try other setting here
        # self.battery_start = self.storage.start
        battery_constraints = self.get_battery_constraints(predicted_load)  # battery constraints
        objective_mode = "Electricity Cost"  # Need to update objective modes to include cost function design
        linear_aging_cost = 0  # based on simple model and predicted control actions - Change this to zero
        electricity_cost = build_electricity_cost(self, predicted_load, price_vector)  # based on prediction as well
        objective = build_objective(objective_mode, electricity_cost, linear_aging_cost)
        opt_problem = Optimization(objective_mode, objective, battery_constraints, predicted_load, self.resolution, None,
                                   self.storage, time=0, name="Test_Case_" + str(self.storage.id))
        opt_problem.run()
        if opt_problem.problem.status != 'optimal':
            print('Unable to service travel')
        if electricity_cost.value < 0:
            print('Negative Electricity Cost')

        control_action = self.battery_current.value[0, 0]   # this is current flowing through each cell
        self.actions.append(control_action)
        self.storage.update_capacity()  # to track linear estimated aging
        # obtain the true state of charge from the batteryAgingSim (How frequently though?)
        if len(self.storage.control_current) < num_steps:
            self.storage.control_current = np.append(self.storage.control_current, control_action)
        else:
            self.storage.control_current = np.array([control_action])  # it should be only updating one but then
            # print("RESETTING CONTROL ARRAY", len(self.storage.control_current))
        self.battery_initial_SOC = self.battery_SOC.value[1, 0]     # update SOC estimation
        return control_action

    def predict_load(self, start, shift, stop, days_length=14):
        """this uses two ML models for predictions. One for full day prediction (runs only once a day) and the
        other for time-step update"""
        begin = stop * 96 - 48 + shift  # shift at each time-step, then reset after a day is done
        end = begin + 48
        test_input_onestep = np.reshape(self.scaled_onestep_data[begin:end], (1, 48, 1))
        if not self.full_day_prediction.any():   # This checks if a full day is done
            test_input_fullday = np.reshape(self.reshaped_data[start:start + days_length * num_steps],
                                            (1, days_length, num_steps))
            self.full_day_prediction = LSTM1.predict(test_input_fullday) * self.std_data + self.mean_data
            self.full_day_prediction.shape = (num_steps, 1)
        prediction_next_step = self.scaler_onestep.inverse_transform(LSTM2.predict(test_input_onestep))
        index = len(self.load) + 1  # this is tracking what time step we are at
        prediction = np.append(self.load, prediction_next_step)     # include previous day's known load
        prediction = np.append(prediction, self.full_day_prediction[index:, :])
        return prediction

    def get_battery_constraints(self, EV_load):
        eps = 0.01  # This is a numerical artifact. Values tend to solve at very low negative values but
        # this helps avoid it.
        # TODO: UPDATE CONTROLLER TO BE MORE INFORMED OF THE VOLTAGE DYNAMICS WITH SOC TO ESTIMATE THE ACTUAL POWER NEEDED
        num_cells_series = self.storage.topology[0]
        num_modules_parallel = self.storage.topology[1]     # maybe make this easier later?? abstract it out
        num_cells = self.storage.topology[2]
        self.storage_constraints = [self.battery_SOC[0] == self.battery_initial_SOC,
                            self.battery_OCV == OCV_SOC_linear_params[0] * self.battery_SOC[0:num_steps] + OCV_SOC_linear_params[1],
                            cp.pos(self.battery_current) <= self.storage.max_current,
                            self.battery_voltage == self.battery_OCV + cp.multiply(self.battery_current, 0.076),
                            self.battery_power == cp.multiply(self.storage.max_voltage * num_cells_series,
                                                      self.battery_current * num_modules_parallel) / 1000,  # kw
                            self.battery_power == self.battery_power_charge + self.battery_power_discharge,
                            self.battery_SOC[1:num_steps + 1] == self.battery_SOC[0:num_steps] \
                                    + (self.resolution / 60 * self.battery_current) / self.storage.cap, # removed self-discharge
                            self.battery_power_discharge <= 0,
                            self.battery_power_charge >= 0,
                            self.battery_SOC >= self.storage.min_SOC,
                            self.battery_SOC <= self.storage.max_SOC,
                            EV_load + (self.battery_power_charge + self.battery_power_discharge) -
                                    solar_gen[self.storage.start:self.storage.start + num_steps] >= eps
                            # no injecting back to the grid; should try unconstrained. This could be infeasible.
                            ]
        return self.storage_constraints

    def reset_load(self):
        """This is done after one full day is done."""
        self.load = np.array([])
        self.full_day_prediction = np.array([])


class MPC2:
    """this uses a different prediction and control mechanism..to be developed later"""
    pass
