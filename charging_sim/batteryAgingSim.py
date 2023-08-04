""" This file hosts the battery degradation module as described in Balogun Et. Al:
https://doi.org/10.36227/techrxiv.23596725.v2 """
import numpy as np
import math


# TODO: How to model power delivery as a function of time, temperature, SOC and SOH at every time step. Cap as a...
#  Is there a relationship between controls cost (temp control etc) with increasing power delivery by battery?
#  How can we incorporate this response delay due to chemistry or reaction responsiveness into simulation?
#  Think about creating a few degradation models from literature.
#  Need to obtain battery discharge curves for real simulation; can we get sample discharge curves or is it necessary?
#  Need to consider Peukert's law: https://en.wikipedia.org/wiki/Peukert%27s_law
#  How does solution improve with horizon length (how far is looking ahead important (for MPC no point but computation
#  can become expensive. Horizon length 'N' vs. optimal savings analysis.
#  I see how RL can be used here to learn a policy with time. Opt. Algo with improve itself based on feedback from aging
#  sim module.


class BatterySim:
    """Current aging model is for LiNiMnCoO2 battery cells. Need to populate more aging models for analyses.
    Link to Paper: https://www.sciencedirect.com/science/article/pii/S0378775314001876

    Params:
        beta_cap: capacity fade aging factor for cycle aging
        alpha_cap capacity fade aging factor for calendar aging
        beta_res: resistance growth aging factor for cycle aging
        alpha_res: resistance growth aging factor for calendar aging
    Assumptions:
        Homogenous battery with dynamics modelled as one big cell.
        Constant temperature profile in vicinity of battery.
        """

    def __init__(self, datetime, num_steps, res=15):
        self.num_steps = num_steps
        # self.battery_objects = battery_objects  # storing all battery objects in a list maintained by sim
        self.time = 1  # because calendar aging is in days, so for each num time_steps for resolution
        self.ambient_temp = 23 + 273  # absolute temp in K
        self.aging_params = {}  # to be updated later
        self.num_daily_steps = 96  # to be configured later
        self.res = res  # minutes
        self.cap = 4.85  # Ah
        self.beta_caps = []
        # BM = BatteryMaps()
        # self.response_surface = BM.get_response_surface()[0]  # this is used to infer voltage from other states
        # Include battery chemistry later.

    def get_cyc_aging(self, battery):
        """Detailed aging for simulation environment. Per Johannes Et. Al"""
        SOC_vector = np.array(
            battery.SOC_list[-(self.num_steps + 1):])  # change this to the list? Done after one complete day
        # print("SOC is: ", SOC_vector)
        # TODO: changed del DOD to absolute value!!
        # print('SOC: ', SOC_vector)
        del_DOD = np.abs(np.round(SOC_vector[0:self.num_steps] - SOC_vector[1:], 5))  # just for numerical convenience. Have this list be updated, given the resolution we want to solve!!!
        # print("del DOD: ", del_DOD)
        # print("Current Voltage is ", battery.voltage)
        # del_DOD[del_DOD < 0] = 0  # remove the charging parts in profile to get DOD
        del_DOD = np.sum(del_DOD) / 2   # total cycle depth is half one depth - DOUBLE-CHECK IF THIS IS ACCURATE
        # del_DOD = np.max(SOC_vector) - np.min(SOC_vector) # this is not entirely accurate
        real_voltage = np.array(battery.voltages[-2:]) / battery.topology[0]    # this should include the prior voltage no?
        avg_voltage = np.sqrt(np.average(real_voltage ** 2))  # quadratic mean voltage for aging
        # print("average voltage: ", avg_voltage, "regular avg: ", np.average(real_voltage))
        beta_cap = 7.348 * 10**-3 * (avg_voltage - 3.695)**2 + 7.6 * 10**-4 + 4.081 * 10**-3 * del_DOD
        beta_cap /= 4880.285045
        # print('Beta Cap is, ', beta_cap)
        beta_res = 2.153 * 10**-4 * (avg_voltage - 3.725)**2 - 1.521 * 10**-5 + 2.798 * 10**-4 * del_DOD
        beta_minimum = 1.5 * 10**-5
        if beta_res < beta_minimum:
            beta_res = beta_minimum  # there is a minimum aging factor that needs to be fixed
        Q = np.abs(battery.current / battery.topology[1] * self.res / 60)  # in Ah

        capacity_fade = beta_cap * Q ** 0.5     # time is one day for both
        # capacity_fade = beta_cap * Qmax**0.5 * 2.15 / battery.cell_nominal_cap # time is one day for both
        battery.cycle_aging.append(capacity_fade)
        # print('Q: {}, del DOD: {}, cap fade: {}'.format(Q, del_DOD, capacity_fade))
        resistance_growth = beta_res * Q
        # resistance_growth = beta_res * Q * 2.15 / battery.cell_nominal_cap
        battery.true_capacity_loss = capacity_fade
        self.beta_caps.append(beta_cap)
        # print("Aging factor beta,", beta_cap)
        # change return function to np.sum later after debugging
        return capacity_fade, np.sum(resistance_growth)

    def update_capacity(self, battery):
        """This uses the electrochemical and impedance-based model per Johannes et. Al"""
        cap_fade = self.get_aging_value(battery)[0]
        battery.SOH -= cap_fade  # change this to nom rating
        battery.SOH_track += battery.SOH,
        battery.cap = battery.SOH * battery.cell_nominal_cap
        battery.Qmax = battery.max_SOC * battery.cap
        battery.true_capacity_loss += cap_fade
        battery.true_aging.append(cap_fade)

    def update_resistance(self, battery):
        """This uses the electrochemical and impedance-based model per Johannes et. Al"""
        res_growth = self.get_aging_value(battery)[1]
        battery.R_cell += res_growth
        battery.resistance_growth += res_growth

    def get_calendar_aging(self, battery):
        """Estimates the calendar aging of the battery using Schmalsteig Model (Same as above)"""
        voltages = np.array(battery.voltages[-2:]) / battery.topology[0]   # this should include the prior voltage no?
        avg_voltage = np.average(voltages)  # mean voltage for aging
        # I THINK THIS MUST BE ESTIMATED IN DAYS? AND I THINK IT HAS TO BE CUMULATIVE? NOT PER TIMESTEP?
        alpha_cap = (7.543 * avg_voltage - 23.75) * 10**6 * math.exp(-6976 / self.ambient_temp)  # aging factors
        alpha_res = (5.270 * avg_voltage - 16.32) * 10**5 * math.exp(-5986 / self.ambient_temp)  # temp in K
        alpha_cap /= 4880.285045  # scaling factor to match our data
        capacity_fade = alpha_cap * (self.time / self.num_daily_steps)**0.75
        # for each time-step (this is scaled up for current cell)
        resistance_growth = alpha_res * (self.time / self.num_daily_steps) ** 0.75
        # for each time-step (this is scaled up for current cell)
        # battery.true_capacity_loss = capacity_fade  # this is wrong
        if isinstance(capacity_fade, float):
            battery.calendar_aging.append(capacity_fade)
            return capacity_fade, resistance_growth  # due to interval which degradation is implemented
        return sum(capacity_fade), sum(resistance_growth)

    def get_total_aging(self, battery):
        return self.get_cyc_aging(battery) + self.get_calendar_aging(battery)

    def get_aging_value(self, battery):
        """returns the actual aging value lost after a cvxpy run..."""
        cap_fade_cycle, res_growth_cycle = self.get_cyc_aging(battery)  # this infers first
        cap_fade_calendar, res_growth_cal = self.get_calendar_aging(battery)
        # print("Cycle aging: {}, Calendar aging: {}".format(cap_fade_cycle, cap_fade_calendar))
        capacity_fade = cap_fade_cycle + cap_fade_calendar
        res_growth = res_growth_cal + res_growth_cycle
        return [capacity_fade, res_growth]

    def run(self, battery):
        """need to slightly change this for now
        MAKE SURE EACH AGING SIM HAS A RUN FUNCTION EACH TIME"""
        self.update_capacity(battery)
        # print("Battery Aging and Response Estimated")

    @staticmethod
    def NMC_cal_aging():
        pass

    @staticmethod
    def NMC_cyc_aging():
        pass

    @staticmethod
    def LFP_cal_aging():
        pass

    @staticmethod
    def LFP_cyc_aging():
        pass
