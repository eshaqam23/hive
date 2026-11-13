# Robot CAD models

Hive's RC car models are assembled from real ROS / Gazebo meshes. Every file here was converted from
its source (Collada or STL) to meshopt-compressed glTF binary, simplified with meshoptimizer to load
on a web page, and stored in ROS coordinates (x forward, y left, z up, metres). Payloads are mounted
on the car in `src/robotModels.js`; nothing else about the geometry was changed.

| File | Model | Author / source | Licence |
| --- | --- | --- | --- |
| `car.glb` | NXP MR-Buggy3 (BaseReduced, TopReduced and Wheel meshes, placed by its model.sdf) | Benjamin Perseghetti, Rudis Laboratories, via Gazebo Fuel: https://app.gazebosim.org/RudisLaboratories/fuel/models/MR-Buggy3 | CC BY 4.0, https://creativecommons.org/licenses/by/4.0/ |
| `d435.glb` | Intel RealSense D435 camera (`d435.dae`) | Intel Corporation, realsense2_description: https://github.com/IntelRealSense/realsense-ros | Apache License 2.0 |
| `lds.glb` | LDS-01 2D lidar (`lds.stl`) | ROBOTIS, turtlebot3_description: https://github.com/ROBOTIS-GIT/turtlebot3 | Apache License 2.0 |
| `arm.glb` | OpenMANIPULATOR-X arm and gripper (link1-5 and palm meshes, assembled from open_manipulator_x.urdf at a stowed pose) | ROBOTIS, open_manipulator_description: https://github.com/ROBOTIS-GIT/open_manipulator | Apache License 2.0 |

The Apache License 2.0 text is in `LICENSE-Apache-2.0.txt`.
