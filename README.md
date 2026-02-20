# Smart Home League Simulation Project

Welcome to the Smart Home League simulation workspace! This project provides a comprehensive environment for students to explore, learn, and compete in the field of smart home robotics using Webots.

Official website: [https://smarthomerobot.ir/](https://smarthomerobot.ir/)

## About the Smart Home League
The Smart Home League is dedicated to inspiring the next generation of innovators in smart home technology. Our platform is designed for students to participate in engaging competitions, develop practical robotics and programming skills, and collaborate with a community passionate about the future of intelligent living spaces.

## Project Structure
- **controllers/**: Contains robot and supervisor controller code.
- **examples/**: Sample robot controller scripts that demonstrate how to interact with the simulation and use the helper library.
- **plugins/**: Custom physics and robot window plugins for Webots.
- **protos/**: Custom Webots PROTO files for smart home elements.
- **worlds/**: Webots world files for different competition scenarios.

## Requirements
- **Webots 2025a**
- **Python 3.12** (recommended)

## Installing the Helper Library
The example controllers rely on a helper library to simplify robot programming. To install it, run:

```bash
pip install git+https://github.com/smart-home-league/smarthome_robot.git
```

If you encounter permission issues, you may use:

```bash
pip install git+https://github.com/smart-home-league/smarthome_robot.git --break-system-packages
```

## Using the Examples
The `examples/` folder contains basic robot controller scripts. These are designed to be assigned to your robot in the Webots simulation via the Web UI. Each script demonstrates fundamental robot behaviors and interactions with the smart home environment, making them ideal starting points for students and competition participants.

You can access the Web UI by right-clicking on the `DEF SUPERVISOR Robot` in the left panel and selecting "Show Robot Window".


## Getting Started
1. Install Webots 2025a and Python 3.12.
2. Clone this repository and install the helper library as described above.
3. Open one of the provided world files in Webots.
4. Assign a controller from the `examples/` folder to your robot using the Webots Web UI.
5. Run the simulation and start exploring smart home robotics!

## Join the Community
Become part of the Smart Home League and help shape the future of smart living. Whether you’re just starting out or looking to test your skills, there’s a place for you here.

Visit our website for more information, resources, and updates: [https://smarthomerobot.ir/](https://smarthomerobot.ir/)

