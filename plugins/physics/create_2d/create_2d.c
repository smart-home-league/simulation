/*
 * Physics plugin: constrain Create robot (DEF VACUUM) to 2D plane.
 * Locks Z position, roll, and pitch - robot moves only in XY with yaw rotation.
 * Handles dynamically added robot (created by supervisor after simulation start).
 */

#include <plugins/physics.h>
#include <math.h>

#define ROBOT_Z_HEIGHT 0.0442

static dBodyID vacuum_body = NULL;
static dJointID plane2d_joint = NULL;
static dWorldID world_id = NULL;

void webots_physics_init() {
  /* Robot is added dynamically, so we setup in webots_physics_step */
}

void webots_physics_step() {
  dBodyID body = dWebotsGetBodyFromDEF("VACUUM");
  if (body != NULL) {
    if (vacuum_body == NULL) {
      /* Robot just appeared - attach Plane2D joint */
      vacuum_body = body;
      world_id = dBodyGetWorld(vacuum_body);
      plane2d_joint = dJointCreatePlane2D(world_id, 0);
      dJointAttach(plane2d_joint, vacuum_body, 0);
    } else if (body != vacuum_body) {
      /* New robot instance (reset) - body pointer changed */
      if (plane2d_joint != NULL) {
        dJointDestroy(plane2d_joint);
        plane2d_joint = NULL;
      }
      vacuum_body = body;
      world_id = dBodyGetWorld(vacuum_body);
      plane2d_joint = dJointCreatePlane2D(world_id, 0);
      dJointAttach(plane2d_joint, vacuum_body, 0);
    }

    /* Correct Z height (Plane2D locks to Z=0, robot needs to be at ground level) */
    const dReal *pos = dBodyGetPosition(vacuum_body);
    dBodySetPosition(vacuum_body, pos[0], pos[1], ROBOT_Z_HEIGHT);

    /* Force upright: extract yaw, set rotation to (0,0,1,yaw) only */
    const dReal *rot = dBodyGetRotation(vacuum_body);
    dReal yaw = atan2(rot[4], rot[0]);
    dMatrix3 R;
    dRFromAxisAndAngle(R, 0, 0, 1, yaw);
    dBodySetRotation(vacuum_body, R);

    /* Zero roll/pitch angular velocity - keep only yaw */
    const dReal *avel = dBodyGetAngularVel(vacuum_body);
    dBodySetAngularVel(vacuum_body, 0, 0, avel[2]);
  } else {
    /* Robot removed */
    vacuum_body = NULL;
    plane2d_joint = NULL;
    world_id = NULL;
  }
}

int webots_physics_collide(dGeomID g1, dGeomID g2) {
  return 0; /* Use default collision handling */
}

void webots_physics_cleanup() {
  if (plane2d_joint != NULL && vacuum_body != NULL) {
    dJointDestroy(plane2d_joint);
  }
  plane2d_joint = NULL;
  vacuum_body = NULL;
  world_id = NULL;
}
