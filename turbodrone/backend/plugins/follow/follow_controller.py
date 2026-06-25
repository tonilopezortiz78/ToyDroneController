class FollowController:
    """
    Calculates yaw/pitch commands to keep a target centered and at a stable distance.
    
    Uses constant-rate (bang-bang) control: outputs fixed command values when
    the target is outside the deadzone, zero when inside.
    """

    def __init__(
        self,
        yaw_deadzone: float = 0.15,
        pitch_deadzone: float = 0.02,
        min_box_width: float = 0.30,
        max_box_width: float = 0.80,
        invert_yaw: bool = False,
        invert_pitch: bool = False,
        yaw_speed: float = 20.0,
        pitch_speed: float = 20.0,
        vert_deadzone: float = 0.10,
        vert_speed: float = 10.0,
        invert_vert: bool = False,
    ):
        self.yaw_deadzone = yaw_deadzone
        self.pitch_deadzone = pitch_deadzone
        self.min_box_width = min_box_width
        self.max_box_width = max_box_width
        self.invert_yaw = invert_yaw
        self.invert_pitch = invert_pitch
        self.yaw_speed = min(100.0, max(0.0, yaw_speed))
        self.pitch_speed = min(100.0, max(0.0, pitch_speed))
        self.vert_deadzone = vert_deadzone
        self.vert_speed = min(100.0, max(0.0, vert_speed))
        self.invert_vert = invert_vert

    def compute(self, box_center_x: float, box_center_y: float, box_width: float) -> tuple[float, float, float]:
        """
        Compute yaw, pitch, and throttle commands.
        
        Args:
            box_center_x: Normalized x position of box center (0.0 = left, 1.0 = right)
            box_center_y: Normalized y position of box center (0.0 = top, 1.0 = bottom)
            box_width: Normalized width of box (0.0 to 1.0)
        
        Returns:
            (yaw, pitch, throttle) commands in range -100 to 100
        """
        # Yaw: rotate to center the target horizontally
        yaw = 0.0
        error_x = box_center_x - 0.5
        if abs(error_x) > self.yaw_deadzone:
            yaw = self.yaw_speed if error_x > 0 else -self.yaw_speed
            if self.invert_yaw:
                yaw = -yaw

        # Pitch: move forward/backward to keep target at desired size
        pitch = 0.0
        if box_width < (self.min_box_width - self.pitch_deadzone):
            pitch = self.pitch_speed  # too far, move forward
        elif box_width > (self.max_box_width + self.pitch_deadzone):
            pitch = -self.pitch_speed  # too close, move backward
        if self.invert_pitch:
            pitch = -pitch

        # Throttle: adjust altitude to keep the target vertically centered
        throttle = 0.0
        error_y = box_center_y - 0.5
        if abs(error_y) > self.vert_deadzone:
            throttle = self.vert_speed if error_y < 0 else -self.vert_speed
            if self.invert_vert:
                throttle = -throttle

        return yaw, pitch, throttle
