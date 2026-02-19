from typing import List, Tuple, Union

X = 0
Y = 1

# Physical ground size in meters (matches world: 20m x 20m)
GROUND_X = 20.0
GROUND_Y = 20.0


def _point_in_polygon(px: float, py: float, polygon: list) -> bool:
    """Ray-casting: point (px, py) inside polygon (list of (x,y) vertices)."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _cell_center_to_world(
    ix: int,
    iy: int,
    cell_size: int,
    ground_size: Union[Tuple[int, int], List[int]],
) -> tuple:
    """
    Convert grid cell (ix, iy) to world (x, y) in meters (center of cell).

    `ground_size` is the display size in pixels; GROUND_X/Y are the physical extents in meters.
    """
    pixel_x = (ix + 0.5) * cell_size
    pixel_y = (iy + 0.5) * cell_size
    width = float(ground_size[X])
    height = float(ground_size[Y])

    world_x = (pixel_x / width) * GROUND_X - GROUND_X / 2.0
    world_y = GROUND_Y / 2.0 - (pixel_y / height) * GROUND_Y
    return (world_x, world_y)


def _world_to_grid(
    world_x: float,
    world_y: float,
    cell_size: int,
    ground_size: Union[Tuple[int, int], List[int]],
) -> tuple:
    """
    Convert world (x,y) in meters to grid (ix, iy).

    `ground_size` is the display size in pixels; GROUND_X/Y are the physical extents in meters.
    """
    width = float(ground_size[X])
    height = float(ground_size[Y])

    pixel_x = width * (world_x + GROUND_X / 2.0) / GROUND_X
    pixel_y = height * (-world_y + GROUND_Y / 2.0) / GROUND_Y
    ix = int(pixel_x / cell_size)
    iy = int(pixel_y / cell_size)
    return (ix, iy)
