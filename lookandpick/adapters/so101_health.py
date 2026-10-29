"""Read-only motor health. Never use follower.connect(): it configures and writes motors."""
from __future__ import annotations

from .so101 import JOINTS


class ReadOnlySO101Health:
    def __init__(self, port, *, bus_factory=None):
        self.port, self._factory = port, bus_factory
        self._bus = None

    def open(self):
        if self._bus is not None:
            raise RuntimeError('Health reader already open')
        if self._factory is None:
            from lerobot.motors import Motor, MotorNormMode
            from lerobot.motors.feetech import FeetechMotorsBus  # LeRobot 0.6 has no SerialMotorsBus
            motors = {name: Motor(i, 'sts3215', MotorNormMode.RANGE_0_100 if name == 'gripper' else MotorNormMode.DEGREES)
                      for i,name in enumerate(JOINTS,1)}
            self._bus = FeetechMotorsBus(port=self.port, motors=motors)
        else:
            self._bus = self._factory(self.port)
        try:
            self._bus.connect(handshake=False)
        except Exception:
            self.close()
            raise

    def read(self):
        if self._bus is None:
            raise RuntimeError('Health reader is not open')
        rows = []
        for expected_id, name in enumerate(JOINTS,1):
            row = {'joint':name, 'expected_id':expected_id, 'errors':[]}
            for key, register in [('id','ID'), ('voltage_raw','Present_Voltage'), ('temperature_c','Present_Temperature')]:
                try:
                    row[key] = self._bus.read(register, name, normalize=False)
                except Exception as exc:
                    row[key] = None
                    row['errors'].append(f'{register}: {exc}')
            row['voltage_v'] = None if row['voltage_raw'] is None else row['voltage_raw'] / 10
            if row['id'] != expected_id:
                row['errors'].append('Expected motor ID did not match')
            rows.append(row)
        return rows

    def close(self):
        if self._bus is not None:
            try:
                self._bus.disconnect(disable_torque=False)  # no implicit Torque_Enable writes
            finally:
                self._bus = None
