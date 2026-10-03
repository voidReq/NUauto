# Category prompt: what kind of role is this?

For each job in your assigned `work/cat_in_NNN.json`, pick exactly ONE `category`
for what the role mainly is (judge from title, function, skills and the description
excerpt; do not invent):

- `security`: cybersecurity is a core part of the job: pentesting, offensive/red
  team, appsec/product security, security engineering, SOC/detection/incident
  response, vulnerability research, network/cloud security, IAM, security
  operations, security-focused GRC with technical work. If security is central,
  choose `security` even when the role is also embedded/hardware/software.
- `embedded`: firmware, microcontrollers, RTOS, device drivers, embedded Linux.
- `hardware`: circuits, PCB, electrical/power, RF/analog, chip/FPGA/ASIC/SoC design
  or verification, electrical engineering.
- `systems`: OS/kernel, networking stacks, compilers, performance, distributed
  systems infrastructure, low-level C/C++ systems software.
- `robotics_test`: robotics, controls, autonomy, hardware test/validation, test
  automation of physical systems, lab/manufacturing test engineering.
- `fullstack_web`: the job is mainly web/app development: full-stack, front-end,
  web back-end/APIs for web products, UI, mobile apps.
- `software`: other software engineering (tools, internal software, backend that is
  not mainly web, general SWE) that fits none of the above.
- `data_ml`: data science/analytics, ML/AI modeling, data engineering.
- `it`: IT support, IT operations, sysadmin, help desk, AV/IT.
- `other`: anything else (product, operations, manufacturing, research outside
  the above).

Write `work/cat_out_NNN.json` (same NNN): a JSON list with exactly one object per
input job, same ids, no extras:

    [{"id": "<job id>", "category": "security"}, ...]

Output valid JSON only in that file. Do not edit any other file.
