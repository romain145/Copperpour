# Linux test harness for session.py: KiCad's official image, a virtual X server (Xvfb) and,
# optionally, a reparenting window manager (openbox). Build from the skill's root folder:
#   docker build -f tests/linux-docker/Dockerfile -t kicad-session-test .
# See README.md for running it.
FROM kicad/kicad:10.0
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb x11-utils openbox \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/session.py tests/linux-docker/mcp.py tests/linux-docker/run_tests.sh /opt/test/
COPY tests/linux-docker/fixtures /opt/fixtures
RUN chmod +x /opt/test/*.sh /opt/test/*.py && chown -R kicad:kicad /opt/fixtures
USER kicad
