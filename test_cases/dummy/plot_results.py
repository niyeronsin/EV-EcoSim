import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
import os


os.chdir('/home/ec2-user/EV50_cosimulation/test_cases/dummy/')
voltages = pd.read_csv('voltages.csv')

#process voltages
cols=list(voltages.columns)
real_cols_ind=[(('_Ar' in m )or('_Br' in m )or('_Cr' in m )) for m in cols]
imag_cols_ind=[(('_Ai' in m )or('_Bi' in m )or('_Ci' in m )) for m in cols]
real_cols=[x for x, y in zip(cols, real_cols_ind) if y == True]
imag_cols=[x for x, y in zip(cols, imag_cols_ind) if y == True]


voltage_real=voltages.loc[:,real_cols_ind]
voltage_imag=voltages.loc[:,imag_cols_ind]

voltage_real=np.asarray(voltage_real)
voltage_imag=np.asarray(voltage_imag)


#calculate complex voltage, mag, phase
voltage_complex=voltage_real+voltage_imag*1j
v=abs(voltage_complex)
a=np.rad2deg(np.angle(voltage_complex))


a[a<50]=a[a<50]+120
a[a>50]=a[a>50]-120

mean_v=np.mean(v,axis=0)

nom_v=np.zeros(mean_v.shape)

nom_v[(mean_v<300)*(mean_v>250)]=480/(3**0.5)
nom_v[(mean_v<8000)*(mean_v>6500)]=7200
nom_v[(mean_v<2600)*(mean_v>2100)]=2401.7
nom_v[(mean_v<150)*(mean_v>80)]=120

norm_v=v/nom_v.reshape(1,-1)

print('Dim v: '+str(v.shape))
print('Dim 480 V: '+str(len(mean_v[(mean_v<300)*(mean_v>250)])))
print('Dim 7200 V: '+str(len(mean_v[(mean_v<8000)*(mean_v>6500)])))
print('Dim 120 V: '+str(len(mean_v[(mean_v<150)*(mean_v>80)])))
print('Dim 2400 V: '+str(len(mean_v[(mean_v<2600)*(mean_v>2100)])))

print(norm_v.shape)
print(v.shape)
print(a.shape)


plt.figure(figsize=(10*0.8,6*0.8))
for i in range(v.shape[1]):       
    plt.plot(v[:,i]/1000)

plt.ylabel('Voltage (V)',fontsize=16)
plt.xlabel('Time (min)',fontsize=16)
ax=plt.gca()
ax.tick_params(axis='both', which='major', labelsize=16)
plt.grid()
plt.show()
plt.savefig('v.png')


plt.figure(figsize=(10*0.8,6*0.8))
for i in range(v.shape[1]):       
    plt.plot(norm_v[:,i])
plt.ylabel('Voltage (p.u.)',fontsize=16)
plt.xlabel('Time (min)',fontsize=16)
ax=plt.gca()
ax.tick_params(axis='both', which='major', labelsize=16)
plt.grid()
plt.show()
plt.savefig('vnorm.png')


plt.figure(figsize=(10*0.8,6*0.8))
plt.hist(np.ndarray.flatten(norm_v),bins=100,range=[0.7,1.08])
plt.ylabel('Count',fontsize=16)
plt.xlabel('Voltage (p.u.)',fontsize=16)
ax=plt.gca()
ax.tick_params(axis='both', which='major', labelsize=16)

plt.xlim([0.7,1.08])
plt.grid()
plt.show()
plt.savefig('vhist.png')


plt.figure(figsize=(10*0.8,6*0.8))
plt.hist(np.ndarray.flatten(a),bins=100,range=[-15,5])
plt.ylabel('Count',fontsize=16)
plt.xlabel('Voltage phase (deg)',fontsize=16)
ax=plt.gca()
ax.tick_params(axis='both', which='major', labelsize=16)
plt.grid()
plt.show()
plt.savefig('ahist.png')