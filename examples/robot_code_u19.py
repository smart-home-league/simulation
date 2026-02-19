"""
U19 example controller: uses distance sensors (primary) and bumpers (fallback),
GPS, IMU, receiver, LEDs, wheel encoders.
Copy to controllers/robot/robot.py and set world subleague to U19.
Requires: pip install smarthome-robot (or from GitHub)
"""

import math
import random
import sys
from smarthome_robot import RobotU19

DISTANCE_LOW = 0.5  # Turn when raw value below this (low = near edge; 70/50≈1.4, 110/50≈2.2 from ref)
MAX_SPEED = 25.0
HALF_SPEED = 12.5
WHEEL_RADIUS = 0.031
AXLE_LENGTH = 0.271756

robot = RobotU19(team_name="U19 Example")
robot.led_on = True


def step() -> None:
    if robot.step(robot.time_step) == -1:
        sys.exit(0)


def move(left: float, right: float) -> None:
    robot.left_motor = left
    robot.right_motor = right


def turn(angle_rad: float) -> None:
    move(0, 0)
    step()
    l0, r0 = robot.left_encoder, robot.right_encoder
    sign = 1.0 if angle_rad >= 0 else -1.0
    move(sign * HALF_SPEED, -sign * HALF_SPEED)
    while True:
        dl = (robot.left_encoder - l0) * WHEEL_RADIUS
        dr = (robot.right_encoder - r0) * WHEEL_RADIUS
        theta = sign * (dl - dr) / AXLE_LENGTH
        if theta >= abs(angle_rad):
            break
        step()
    move(0, 0)
    step()


def back_and_turn() -> None:
    move(-HALF_SPEED, -HALF_SPEED)
    for _ in range(15):
        step()
    turn(random.choice([-1, 1]) * (0.5 + random.random()) * math.pi)


random.seed()
duration = 0
rotation = False

while True:
    if duration > 0:
        duration -= 1

    elif robot.bumper_left != 0.0 or robot.bumper_right != 0.0:
        move(-MAX_SPEED, MAX_SPEED)
        duration = 50

    elif rotation and (robot.distance_front_left < DISTANCE_LOW or robot.distance_front_right < DISTANCE_LOW):
        if random.randint(0, 1) == 0:
            move(MAX_SPEED, -MAX_SPEED)
        else:
            move(-MAX_SPEED, MAX_SPEED)

        duration = 15
        rotation = False

    elif robot.distance_front_left < DISTANCE_LOW and robot.distance_front_right < DISTANCE_LOW:
        move(-MAX_SPEED, -MAX_SPEED)
        duration = 30
        rotation = True

    elif robot.distance_left < DISTANCE_LOW:
        move(HALF_SPEED, -MAX_SPEED)
        duration = 20

    elif robot.distance_right < DISTANCE_LOW:
        move(-MAX_SPEED, HALF_SPEED)
        duration = 20

    else:
        move(MAX_SPEED, MAX_SPEED)

    step()
