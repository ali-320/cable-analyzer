Things to understand

- [ ] Give me the all the grades that you are using, the quality decisions in each grade, and the metrics value that send the cable to each grade.

- [ ] Check with the team about the values of the CH224k.

- [ ] WHat is meant by the controlled load path, and what is PWM.

- [x] What is meant by the Full: probe + charge -> Verdict in the "python -m src.main --length 1.0"

- [x] What is actually meant by probe?

- [x] In the build_hardware() function, while creating the sim_state, why are the variables like r_cable_ohm=float(sim_cfg.get("r_cable_ohm", 0.15)). Why is there a 0.15 entry at the end, is it the default value when reading is from CH-224K is unsuccessful. If yes, at the end of the output, I want you to show that the default values were used.

- [x] I have a warning in the ch224k.py that the RPi.GPIO could not be resolved. Is the name correct? Does this mean that this library won't run.

- [x] What is meant by R-fixture, and is this value need to be hardcoded, meaning not readable through our sensors?

- [x] In VS code, in the run_probe() function. In the parameters it is showing me that sim_state is not accessed (PyLance)

- [x] What is meant by sel0 = 22, sel1 = 23, sel2 = 24

- [x] I again have the warning the ina219 is not resolved in ina219_reader.py. Will this warning go away when the library is installed on raspberry pi.

- [x] You said that it is configured for 32 V bus range, 3.2 A shunt measurement range, and 0.1 ohm shunt resistor. Does this mean, that these values are hardcoded, and can't be computed in real time using the sensor. Do these values need to be given by us?

- [x] Does the sampler rate mean the time in after which the sampler will read the measurement again?

- [x] What is shunt FSR? and does the range of current depends on it? If yes then is the range of current in sampler.py hardcoded? (in the validate_sample function)

- [x] In the metrics.py you are calculating R_loop(s) using the V_target and V_load, where are these values coming from.

- [x] In the metrics.py file, in the return section, tell me what each metrics term represents, and the dependency of each term as well.

- [x] What is the fixture baseline resistance and why is it important.

- [x] What is meant by the limitation value, in the base variable, in the evaluate function, in the rule.py file.

- [x] Why is the grading dependent on the resistance thresholds in config.toml. Does Grade A means good quality wire. And what do you mean by when cable length is configured, do you mean that we can do this even without cable length?

- [x] what is the sigma_v_marginal_mv, and what does it's 25 value means.

- [x] What do you mean by controlling the phone path, can I turn off the charging of the phone?

- [x] What do you mean by during probing?

- [x] What is the final evidence of eta=96.4%

- [x] Why am I specifying the length as 1.0, can't I run it without specifying the length.