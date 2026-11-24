"""
NES APU (Audio Processing Unit) — Simplified
Generates audio via 5 channels: 2 Pulse, 1 Triangle, 1 Noise, 1 DMC.

This is a simplified but functional APU implementation.
Full audio mixing produces mono output at ~44100 Hz sample rate.
"""

import numpy as np

SAMPLE_RATE = 44100
CPU_CLOCK   = 1789773   # ~1.79 MHz


class LengthCounter:
    TABLE = [10,254,20,2,40,4,80,6,160,8,60,10,14,12,26,14,
             12,16,24,18,48,20,96,22,192,24,72,26,16,28,32,30]

    def __init__(self):
        self.counter  = 0
        self.enabled  = True
        self.halted   = False

    def clock(self):
        if not self.halted and self.counter > 0:
            self.counter -= 1

    def load(self, val):
        if self.enabled:
            self.counter = self.TABLE[val]

    def active(self):
        return self.counter > 0


class Envelope:
    def __init__(self):
        self.start    = False
        self.loop     = False
        self.constant = False
        self.volume   = 0
        self.decay    = 0
        self.divider  = 0

    def clock(self):
        if self.start:
            self.start = False
            self.decay = 15
            self.divider = self.volume
        else:
            if self.divider > 0:
                self.divider -= 1
            else:
                self.divider = self.volume
                if self.decay > 0:
                    self.decay -= 1
                elif self.loop:
                    self.decay = 15

    def output(self):
        return self.volume if self.constant else self.decay


class Sweep:
    def __init__(self, channel):
        self.channel  = channel   # 1 or 2 (for two's complement behaviour)
        self.enabled  = False
        self.period   = 0
        self.negate   = False
        self.shift    = 0
        self.reload   = False
        self.divider  = 0

    def clock(self, timer_period):
        target = timer_period >> self.shift
        if self.negate:
            target = timer_period - target - (1 if self.channel == 1 else 0)
        else:
            target = timer_period + target

        if self.divider == 0 and self.enabled and self.shift > 0 and not self._muted(timer_period, target):
            timer_period = target

        if self.divider == 0 or self.reload:
            self.divider = self.period
            self.reload = False
        else:
            self.divider -= 1

        return timer_period

    def _muted(self, period, target):
        return period < 8 or target > 0x7FF

    def muted(self, period):
        target = period >> self.shift
        if not self.negate:
            target = period + target
        else:
            target = period - target
        return period < 8 or target > 0x7FF


class PulseChannel:
    DUTY = [
        [0,1,0,0,0,0,0,0],
        [0,1,1,0,0,0,0,0],
        [0,1,1,1,1,0,0,0],
        [1,0,0,1,1,1,1,1],
    ]

    def __init__(self, channel):
        self.channel   = channel
        self.enabled   = False
        self.duty      = 0
        self.duty_pos  = 0
        self.timer     = 0
        self.timer_period = 0
        self.length    = LengthCounter()
        self.envelope  = Envelope()
        self.sweep     = Sweep(channel)

    def write(self, reg, data):
        if reg == 0:
            self.duty             = (data >> 6) & 3
            self.length.halted    = bool(data & 0x20)
            self.envelope.loop    = bool(data & 0x20)
            self.envelope.constant = bool(data & 0x10)
            self.envelope.volume  = data & 0x0F
        elif reg == 1:
            self.sweep.enabled = bool(data & 0x80)
            self.sweep.period  = (data >> 4) & 0x07
            self.sweep.negate  = bool(data & 0x08)
            self.sweep.shift   = data & 0x07
            self.sweep.reload  = True
        elif reg == 2:
            self.timer_period = (self.timer_period & 0x700) | data
        elif reg == 3:
            self.timer_period = (self.timer_period & 0x0FF) | ((data & 0x07) << 8)
            self.length.load((data >> 3) & 0x1F)
            self.duty_pos = 0
            self.envelope.start = True

    def clock_timer(self):
        if self.timer > 0:
            self.timer -= 1
        else:
            self.timer = self.timer_period
            self.duty_pos = (self.duty_pos + 1) % 8

    def clock_half_frame(self):
        self.sweep.clock(self.timer_period)
        self.length.clock()

    def clock_quarter_frame(self):
        self.envelope.clock()

    def output(self):
        if not self.enabled or not self.length.active():
            return 0
        if self.sweep.muted(self.timer_period):
            return 0
        if not self.DUTY[self.duty][self.duty_pos]:
            return 0
        return self.envelope.output()


class TriangleChannel:
    SEQUENCE = [15,14,13,12,11,10,9,8,7,6,5,4,3,2,1,0,
                 0, 1, 2, 3, 4, 5,6,7,8,9,10,11,12,13,14,15]

    def __init__(self):
        self.enabled      = False
        self.timer        = 0
        self.timer_period = 0
        self.seq_pos      = 0
        self.length       = LengthCounter()
        self.linear_counter = 0
        self.linear_reload  = 0
        self.linear_flag    = False
        self.control_flag   = False

    def write(self, reg, data):
        if reg == 0:
            self.control_flag     = bool(data & 0x80)
            self.length.halted    = bool(data & 0x80)
            self.linear_reload    = data & 0x7F
        elif reg == 2:
            self.timer_period = (self.timer_period & 0x700) | data
        elif reg == 3:
            self.timer_period = (self.timer_period & 0x0FF) | ((data & 0x07) << 8)
            self.length.load((data >> 3) & 0x1F)
            self.linear_flag = True

    def clock_timer(self):
        if self.timer > 0:
            self.timer -= 1
        else:
            self.timer = self.timer_period
            if self.length.active() and self.linear_counter > 0:
                self.seq_pos = (self.seq_pos + 1) % 32

    def clock_half_frame(self):
        self.length.clock()

    def clock_quarter_frame(self):
        if self.linear_flag:
            self.linear_counter = self.linear_reload
        elif self.linear_counter > 0:
            self.linear_counter -= 1
        if not self.control_flag:
            self.linear_flag = False

    def output(self):
        if not self.enabled or not self.length.active() or self.linear_counter == 0:
            return 0
        return self.SEQUENCE[self.seq_pos]


class NoiseChannel:
    PERIOD_TABLE = [4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068]

    def __init__(self):
        self.enabled  = False
        self.timer    = 0
        self.timer_period = 0
        self.mode     = False
        self.shift_reg = 1
        self.length   = LengthCounter()
        self.envelope = Envelope()

    def write(self, reg, data):
        if reg == 0:
            self.length.halted    = bool(data & 0x20)
            self.envelope.loop    = bool(data & 0x20)
            self.envelope.constant = bool(data & 0x10)
            self.envelope.volume  = data & 0x0F
        elif reg == 2:
            self.mode = bool(data & 0x80)
            self.timer_period = self.PERIOD_TABLE[data & 0x0F]
        elif reg == 3:
            self.length.load((data >> 3) & 0x1F)
            self.envelope.start = True

    def clock_timer(self):
        if self.timer > 0:
            self.timer -= 1
        else:
            self.timer = self.timer_period
            bit = 6 if self.mode else 1
            feedback = (self.shift_reg & 1) ^ ((self.shift_reg >> bit) & 1)
            self.shift_reg = (self.shift_reg >> 1) | (feedback << 14)

    def clock_half_frame(self):
        self.length.clock()

    def clock_quarter_frame(self):
        self.envelope.clock()

    def output(self):
        if not self.enabled or not self.length.active() or (self.shift_reg & 1):
            return 0
        return self.envelope.output()


class APU:
    def __init__(self):
        self.pulse1   = PulseChannel(1)
        self.pulse2   = PulseChannel(2)
        self.triangle = TriangleChannel()
        self.noise    = NoiseChannel()

        self.frame_counter = 0
        self.frame_mode    = 0   # 0=4-step, 1=5-step
        self.frame_irq     = False
        self.irq_pending   = False

        self.cycle         = 0
        self.sample_timer  = 0.0
        self.sample_period = CPU_CLOCK / SAMPLE_RATE
        self.output_buffer = []

        # Vectorized audio synthesizer state
        self.p1_phase   = 0.0
        self.p2_phase   = 0.0
        self.tri_phase  = 0.0
        self.noise_table = np.random.uniform(-1.0, 1.0, 32768).astype(np.float32)
        self.noise_idx  = 0

    def write(self, addr, data):
        if addr == 0x4000: self.pulse1.write(0, data)
        elif addr == 0x4001: self.pulse1.write(1, data)
        elif addr == 0x4002: self.pulse1.write(2, data)
        elif addr == 0x4003: self.pulse1.write(3, data)
        elif addr == 0x4004: self.pulse2.write(0, data)
        elif addr == 0x4005: self.pulse2.write(1, data)
        elif addr == 0x4006: self.pulse2.write(2, data)
        elif addr == 0x4007: self.pulse2.write(3, data)
        elif addr == 0x4008: self.triangle.write(0, data)
        elif addr == 0x400A: self.triangle.write(2, data)
        elif addr == 0x400B: self.triangle.write(3, data)
        elif addr == 0x400C: self.noise.write(0, data)
        elif addr == 0x400E: self.noise.write(2, data)
        elif addr == 0x400F: self.noise.write(3, data)
        elif addr == 0x4015:
            self.pulse1.enabled   = bool(data & 0x01)
            self.pulse1.length.enabled = self.pulse1.enabled
            self.pulse2.enabled   = bool(data & 0x02)
            self.pulse2.length.enabled = self.pulse2.enabled
            self.triangle.enabled = bool(data & 0x04)
            self.triangle.length.enabled = self.triangle.enabled
            self.noise.enabled    = bool(data & 0x08)
            self.noise.length.enabled = self.noise.enabled
            if not self.pulse1.enabled:   self.pulse1.length.counter = 0
            if not self.pulse2.enabled:   self.pulse2.length.counter = 0
            if not self.triangle.enabled: self.triangle.length.counter = 0
            if not self.noise.enabled:    self.noise.length.counter = 0
        elif addr == 0x4017:
            self.frame_mode = (data >> 7) & 1
            self.frame_irq  = not bool(data & 0x40)

    def read_status(self):
        status = 0
        if self.pulse1.length.active():   status |= 0x01
        if self.pulse2.length.active():   status |= 0x02
        if self.triangle.length.active(): status |= 0x04
        if self.noise.length.active():    status |= 0x08
        if self.irq_pending:              status |= 0x40
        self.irq_pending = False
        return status

    def clock(self):
        """Called once per CPU cycle (~1.79 MHz)."""
        # Clock channels at their own rates
        self.triangle.clock_timer()
        if self.cycle % 2 == 0:
            self.pulse1.clock_timer()
            self.pulse2.clock_timer()
            self.noise.clock_timer()

        self.frame_counter += 1
        if self.frame_mode == 0:
            steps = [3728, 7456, 11185, 14914]
        else:
            steps = [3728, 7456, 11185, 18640]

        if self.frame_counter == steps[0] + 1 or \
           self.frame_counter == steps[2] + 1:
            self._clock_quarter()

        if self.frame_counter == steps[1] + 1 or \
           self.frame_counter == steps[3] + 1:
            self._clock_quarter()
            self._clock_half()

        if self.frame_mode == 0 and self.frame_counter == 14914:
            if self.frame_irq:
                self.irq_pending = True
            self.frame_counter = 0

        if self.frame_mode == 1 and self.frame_counter == 18641:
            self.frame_counter = 0

        # Sample output
        self.sample_timer -= 1.0
        if self.sample_timer <= 0.0:
            self.sample_timer += self.sample_period
            self.output_buffer.append(self._mix())

        self.cycle += 1

    def _clock_quarter(self):
        self.pulse1.clock_quarter_frame()
        self.pulse2.clock_quarter_frame()
        self.triangle.clock_quarter_frame()
        self.noise.clock_quarter_frame()

    def _clock_half(self):
        self.pulse1.clock_half_frame()
        self.pulse2.clock_half_frame()
        self.triangle.clock_half_frame()
        self.noise.clock_half_frame()

    def _mix(self):
        """Mix all channels into a single normalised float sample [-1, 1]."""
        p1 = self.pulse1.output()
        p2 = self.pulse2.output()
        tri = self.triangle.output()
        noi = self.noise.output()

        # Official NES mixing formula (from nesdev wiki)
        pulse_out = 0.00752 * (p1 + p2) if (p1 + p2) > 0 else 0
        tnd_out   = 0.00851 * tri + 0.00494 * noi if (tri + noi) > 0 else 0
        return pulse_out + tnd_out  # range ~0..1

    def get_samples(self):
        """Return and clear the sample buffer."""
        buf = self.output_buffer[:]
        self.output_buffer.clear()
        return buf

    def generate_frame_audio(self, count=735):
        """Synthesize frame audio in one fast, vectorized pass."""
        # Clock envelopes and length counters for frame
        self._clock_quarter()
        self._clock_half()

        t_steps = np.arange(count, dtype=np.float32)
        mixed = np.zeros(count, dtype=np.float32)

        # Pulse 1
        p1 = self.pulse1
        if p1.enabled and p1.length.active() and p1.timer_period >= 8 and not p1.sweep.muted(p1.timer_period):
            p1_vol = (p1.envelope.output() / 15.0) * 0.35
            if p1_vol > 0:
                p1_freq = CPU_CLOCK / (16.0 * (p1.timer_period + 1))
                step = p1_freq / SAMPLE_RATE
                phases = (self.p1_phase + t_steps * step) % 1.0
                self.p1_phase = (self.p1_phase + count * step) % 1.0
                duty_thresh = (0.125, 0.25, 0.5, 0.75)[p1.duty]
                mixed += np.where(phases < duty_thresh, p1_vol, -p1_vol)

        # Pulse 2
        p2 = self.pulse2
        if p2.enabled and p2.length.active() and p2.timer_period >= 8 and not p2.sweep.muted(p2.timer_period):
            p2_vol = (p2.envelope.output() / 15.0) * 0.35
            if p2_vol > 0:
                p2_freq = CPU_CLOCK / (16.0 * (p2.timer_period + 1))
                step = p2_freq / SAMPLE_RATE
                phases = (self.p2_phase + t_steps * step) % 1.0
                self.p2_phase = (self.p2_phase + count * step) % 1.0
                duty_thresh = (0.125, 0.25, 0.5, 0.75)[p2.duty]
                mixed += np.where(phases < duty_thresh, p2_vol, -p2_vol)

        # Triangle
        tri = self.triangle
        if tri.enabled and tri.length.active() and tri.linear_counter > 0 and tri.timer_period >= 2:
            tri_freq = CPU_CLOCK / (32.0 * (tri.timer_period + 1))
            step = tri_freq / SAMPLE_RATE
            phases = (self.tri_phase + t_steps * step) % 1.0
            self.tri_phase = (self.tri_phase + count * step) % 1.0
            tri_out = np.where(phases < 0.5, 4.0 * phases - 1.0, 3.0 - 4.0 * phases) * 0.35
            mixed += tri_out

        # Noise
        noi = self.noise
        if noi.enabled and noi.length.active():
            noi_vol = (noi.envelope.output() / 15.0) * 0.2
            if noi_vol > 0:
                mixed += self.noise_table[self.noise_idx : self.noise_idx + count] * noi_vol
                self.noise_idx = (self.noise_idx + count) % (len(self.noise_table) - count)

        # Scale to 16-bit integer PCM and output stereo (count, 2)
        int_arr = (np.clip(mixed, -1.0, 1.0) * 24000).astype(np.int16)
        return np.column_stack((int_arr, int_arr))
