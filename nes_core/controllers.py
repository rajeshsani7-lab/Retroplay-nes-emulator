"""Universal USB / Bluetooth Gamepad Manager.

Supports:
- Xbox, PlayStation (DualShock 4 / DualSense), Nintendo Switch Pro, 8BitDo,
  and Logitech controllers via SDL2 GameController API (pygame._sdl2.controller).
- Generic DirectInput / retro USB NES/SNES controllers via pygame.joystick fallback.
- Hot-plugging (connect/disconnect at runtime) and 2-player support.
"""

import pygame

try:
    import pygame._sdl2.controller as sdl_ctrl
    HAS_SDL_CTRL = True
except Exception:
    HAS_SDL_CTRL = False


class GamepadManager:
    """Manages connected controllers for Player 1, Player 2, and Menu navigation."""

    def __init__(self):
        pygame.joystick.init()
        if HAS_SDL_CTRL:
            try:
                sdl_ctrl.init()
            except Exception:
                pass

        # Ordered list of active devices: [{"id": instance_id, "name": str, "ctrl": Controller|None, "joy": Joystick}]
        self.devices = []
        self._prev_menu_mask = 0
        self._prev_pause_combo = False

        for idx in range(pygame.joystick.get_count()):
            self.add_device(idx)

    def add_device(self, device_index: int):
        """Register a newly connected joystick/controller by device index."""
        try:
            joy = pygame.joystick.Joystick(device_index)
            inst_id = joy.get_instance_id()
            if any(d["id"] == inst_id for d in self.devices):
                return None

            ctrl = None
            if HAS_SDL_CTRL and sdl_ctrl.is_controller(device_index):
                try:
                    ctrl = sdl_ctrl.Controller(device_index)
                except Exception:
                    ctrl = None

            name = ctrl.name if ctrl is not None else joy.get_name()
            self.devices.append({"id": inst_id, "name": name, "ctrl": ctrl, "joy": joy})
            return name
        except Exception:
            return None

    def remove_device(self, instance_id: int):
        """Remove a disconnected controller by its SDL instance_id."""
        for i, d in enumerate(self.devices):
            if d["id"] == instance_id:
                name = d["name"]
                try:
                    if d["ctrl"] is not None:
                        d["ctrl"].quit()
                    d["joy"].quit()
                except Exception:
                    pass
                self.devices.pop(i)
                return name
        return None

    def _read_sdl_controller(self, ctrl) -> tuple[int, bool]:
        """Read NES bitmask (A,B,Sel,St,U,D,L,R) and pause-combo from SDL2 Controller."""
        p = 0
        try:
            if ctrl.get_button(pygame.CONTROLLER_BUTTON_DPAD_RIGHT): p |= (1 << 0)
            if ctrl.get_button(pygame.CONTROLLER_BUTTON_DPAD_LEFT):  p |= (1 << 1)
            if ctrl.get_button(pygame.CONTROLLER_BUTTON_DPAD_DOWN):  p |= (1 << 2)
            if ctrl.get_button(pygame.CONTROLLER_BUTTON_DPAD_UP):    p |= (1 << 3)

            ax = ctrl.get_axis(pygame.CONTROLLER_AXIS_LEFTX)
            ay = ctrl.get_axis(pygame.CONTROLLER_AXIS_LEFTY)
            if ax > 12000:  p |= (1 << 0)
            if ax < -12000: p |= (1 << 1)
            if ay > 12000:  p |= (1 << 2)
            if ay < -12000: p |= (1 << 3)

            if ctrl.get_button(pygame.CONTROLLER_BUTTON_START): p |= (1 << 4)
            if ctrl.get_button(pygame.CONTROLLER_BUTTON_BACK):  p |= (1 << 5)
            if ctrl.get_button(pygame.CONTROLLER_BUTTON_B) or ctrl.get_button(pygame.CONTROLLER_BUTTON_X):
                p |= (1 << 6)  # NES B
            if ctrl.get_button(pygame.CONTROLLER_BUTTON_A) or ctrl.get_button(pygame.CONTROLLER_BUTTON_Y):
                p |= (1 << 7)  # NES A

            guide = ctrl.get_button(pygame.CONTROLLER_BUTTON_GUIDE)
            pause_combo = bool(guide) or ((p & 0x30) == 0x30)  # Guide or Select+Start
            return p, pause_combo
        except Exception:
            return 0, False

    def _read_generic_joystick(self, joy) -> tuple[int, bool]:
        """Read NES bitmask from any generic DirectInput / retro USB joystick."""
        p = 0
        try:
            if joy.get_numhats() > 0:
                hx, hy = joy.get_hat(0)
                if hx > 0: p |= (1 << 0)
                if hx < 0: p |= (1 << 1)
                if hy < 0: p |= (1 << 2)
                if hy > 0: p |= (1 << 3)

            if joy.get_numaxes() >= 2:
                ax, ay = joy.get_axis(0), joy.get_axis(1)
                if ax > 0.45:  p |= (1 << 0)
                if ax < -0.45: p |= (1 << 1)
                if ay > 0.45:  p |= (1 << 2)
                if ay < -0.45: p |= (1 << 3)

            nb = joy.get_numbuttons()
            # Face buttons: support both Xbox-style (0=A,1=B,2=X,3=Y) and retro 4-btn (0/1/2/3)
            if nb > 0 and joy.get_button(0): p |= (1 << 7)  # A
            if nb > 1 and joy.get_button(1): p |= (1 << 6)  # B
            if nb > 2 and joy.get_button(2): p |= (1 << 6)  # X -> B
            if nb > 3 and joy.get_button(3): p |= (1 << 7)  # Y -> A

            # Select / Start across XInput (6/7) and generic retro USB pads (8/9)
            if (nb > 6 and joy.get_button(6)) or (nb > 8 and joy.get_button(8)):
                p |= (1 << 5)  # Select
            if (nb > 7 and joy.get_button(7)) or (nb > 9 and joy.get_button(9)):
                p |= (1 << 4)  # Start

            # Some controllers expose D-pad on buttons 11..14
            if nb > 14:
                if joy.get_button(11): p |= (1 << 3)
                if joy.get_button(12): p |= (1 << 2)
                if joy.get_button(13): p |= (1 << 1)
                if joy.get_button(14): p |= (1 << 0)

            pause_combo = ((p & 0x30) == 0x30)
            return p, pause_combo
        except Exception:
            return 0, False

    def read_player(self, player_idx: int) -> tuple[int, bool]:
        """Return (nes_bitmask, pause_triggered) for player 0 or 1."""
        if player_idx >= len(self.devices):
            return 0, False
        dev = self.devices[player_idx]
        if dev["ctrl"] is not None:
            return self._read_sdl_controller(dev["ctrl"])
        return self._read_generic_joystick(dev["joy"])

    def check_pause_pressed(self) -> bool:
        """Edge-triggered check for Guide or Start+Select on Player 1 gamepad."""
        _, combo = self.read_player(0)
        triggered = combo and not self._prev_pause_combo
        self._prev_pause_combo = combo
        return triggered

    def poll_menu_keys(self) -> list[int]:
        """Return synthetic pygame.K_* key events from Player 1 gamepad for menu navigation."""
        mask, _ = self.read_player(0)
        newly_pressed = mask & ~self._prev_menu_mask
        self._prev_menu_mask = mask
        keys = []
        if newly_pressed & (1 << 3): keys.append(pygame.K_UP)
        if newly_pressed & (1 << 2): keys.append(pygame.K_DOWN)
        if newly_pressed & (1 << 1): keys.append(pygame.K_LEFT)
        if newly_pressed & (1 << 0): keys.append(pygame.K_RIGHT)
        if newly_pressed & ((1 << 7) | (1 << 4)): keys.append(pygame.K_RETURN)  # A / Start
        if newly_pressed & (1 << 6): keys.append(pygame.K_ESCAPE)              # B = Back
        if newly_pressed & (1 << 5): keys.append(pygame.K_TAB)                 # Select = Tab
        return keys

    def quit(self):
        for d in self.devices:
            try:
                if d["ctrl"] is not None:
                    d["ctrl"].quit()
                d["joy"].quit()
            except Exception:
                pass
        self.devices.clear()
        if HAS_SDL_CTRL:
            try:
                sdl_ctrl.quit()
            except Exception:
                pass
