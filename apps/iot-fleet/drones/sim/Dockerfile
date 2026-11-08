# ArduPilot SITL + Gazebo Harmonic + the vision-landing-2 AprilTag detector, for the
# P0 hop and precision-landing runs. Everything runs in here; nothing is installed on the host.
FROM ubuntu:24.04

ARG ARDUPILOT_REF=Copter-4.7.2
ARG ARDUPILOT_GAZEBO_SHA=f116b03552d05c8d0b0bed5ed67033a1183121d1
ARG ARUCO_REF=v3.1.0
ARG APRILTAG_REF=v3.4.5
ARG VISION_LANDING_SHA=0c778f608b2e10fb160c0c1f6bdfece7e1b4db34

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates curl gnupg lsb-release pkg-config \
      python3 python3-dev python3-numpy \
      libopencv-dev nlohmann-json3-dev rapidjson-dev \
      libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
      gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
      gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-tools \
      libegl1 libgl1-mesa-dri mesa-utils \
 && curl -fsSL https://packages.osrfoundation.org/gazebo.gpg -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" > /etc/apt/sources.list.d/gazebo-stable.list \
 && apt-get update && apt-get install -y --no-install-recommends gz-harmonic libgz-sim8-dev \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
RUN uv pip install --system --break-system-packages --no-cache empy==3.3.4 pexpect future lxml pymavlink dronecan

# ArduPilot SITL (arducopter binary, no sim_vehicle.py at run time)
RUN git clone --depth 1 -b ${ARDUPILOT_REF} --recurse-submodules --shallow-submodules \
      https://github.com/ArduPilot/ardupilot /opt/ardupilot \
 && cd /opt/ardupilot && ./waf configure --board sitl && ./waf copter \
 && cp build/sitl/bin/arducopter /usr/local/bin/ \
 && cp Tools/autotest/default_params/copter.parm /opt/copter.parm \
 && cp Tools/autotest/default_params/gazebo-iris.parm /opt/gazebo-iris.parm \
 && rm -rf build/sitl/ArduCopter build/sitl/libraries

# gz-transport13's CMake config needs cppzmq, which gz-harmonic does not pull in
RUN apt-get update && apt-get install -y --no-install-recommends cppzmq-dev libzmq3-dev && rm -rf /var/lib/apt/lists/*

# ardupilot_gazebo: the ArduPilotPlugin (JSON link to SITL)
RUN git init /opt/ardupilot_gazebo && cd /opt/ardupilot_gazebo \
 && git fetch --depth 1 https://github.com/ArduPilot/ardupilot_gazebo ${ARDUPILOT_GAZEBO_SHA} && git checkout FETCH_HEAD \
 && mkdir build && cd build && GZ_VERSION=harmonic cmake .. -DCMAKE_BUILD_TYPE=RelWithDebInfo && make -j"$(nproc)"

# aruco (track_targets links it for camera parameters) and vision-landing-2 track_targets.
# Upstream puts -lapriltag in CMAKE_CXX_FLAGS, which lands before the objects on the link
# line and is dropped; CMAKE_CXX_STANDARD_LIBRARIES puts it at the end instead.
# apriltag from source: Ubuntu's libapriltag 3.3 does not export the matd_* helpers the detector calls
RUN git clone --depth 1 -b ${APRILTAG_REF} https://github.com/AprilRobotics/apriltag /opt/apriltag \
 && cmake -S /opt/apriltag -B /opt/apriltag/build -DCMAKE_BUILD_TYPE=Release -DBUILD_PYTHON_WRAPPER=OFF -DBUILD_EXAMPLES=OFF \
 && cmake --build /opt/apriltag/build -j"$(nproc)" && cmake --install /opt/apriltag/build && ldconfig
RUN git clone --depth 1 -b ${ARUCO_REF} https://github.com/fnoop/aruco /opt/aruco \
 && cd /opt/aruco && mkdir build && cd build && cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_UTILS=OFF && make -j"$(nproc)" && make install && ldconfig
RUN git init /opt/vision-landing-2 && cd /opt/vision-landing-2 \
 && git fetch --depth 1 https://github.com/kripper/vision-landing-2 ${VISION_LANDING_SHA} && git checkout FETCH_HEAD \
 && cd src && cmake . -DCMAKE_CXX_STANDARD_LIBRARIES="-lapriltag -latomic" && make -j"$(nproc)" && cp track_targets /usr/local/bin/

ENV GZ_SIM_SYSTEM_PLUGIN_PATH=/opt/ardupilot_gazebo/build \
    GZ_SIM_RESOURCE_PATH=/opt/ardupilot_gazebo/models:/opt/ardupilot_gazebo/worlds:/sim/gazebo/models:/sim/gazebo/worlds \
    PYTHONUNBUFFERED=1
WORKDIR /sim
