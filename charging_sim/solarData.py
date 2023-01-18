import pandas as pd
import numpy as np
from scipy.stats import truncnorm
import os

def sample_solar():
    """SAMPLE_SOLAR Randomly simulate the power output from solar PV panels as a fraction of rated capacity"""

    # r = sample_solar() returns an array  of random simulations of solar PV output where pvpower is a list
    # of predictions of the fraction of rated capacity generated and sigma is a list of the standard deviation
    # associated with each prediction.
    current_dir = os.getcwd()
    path_break_idk = current_dir.index('EV50_cosimulation')
    current_dir = current_dir[0:path_break_idk]+'EV50_cosimulation'
    path_ = os.path.join(current_dir, 'Datasets', 'Mountain View Rooftop Solar.xlsx')

    # read in data
    data = pd.read_excel(path_, sheet_name='Normalized Power', engine='openpyxl')

    pvpower = data.Frac_Power.to_list()
    sigma = data.Sigma.to_list()

    # return list
    r = []

    for t in range(len(pvpower)):
        if pvpower[t] > 0 and sigma[t] > 0:
            # define mean-preserving upper and lower bounds to add +- 15% error
            lb, ub = 0.85 * pvpower[t], 1.15 * pvpower[t]
            # redefine lower and upper bounds as Z-scores for truncnorm
            a, b = (lb - pvpower[t])/sigma[t], (ub - pvpower[t])/sigma[t]
            # use truncnorm to simulate power output as % of rated capacity
            p = truncnorm.rvs(a, b, pvpower[t], sigma[t], 1)
            r.append(float(p))
        else:
            r.append(pvpower[t])
    return np.array(r)


def main():
    """test sample_solar to see if any bias was added to capacity factor"""
    cf = []
    for s in range(10):
        # run sample_solar
        test = sample_solar()
        # resulting capacity factor (should be around 0.16)
        cf.append(sum(test)/len(test))
    print(np.mean(cf))


# test sample_solar #
if __name__ == '__main__':
    main()