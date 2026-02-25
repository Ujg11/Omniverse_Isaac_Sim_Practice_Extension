import os
import carb
import re
import asyncio
import omni.kit.app
import omni.replicator.core as rep
import omni.usd

from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema

from . import global_variables as gv


def _find_xform_by_prefix_latest(stage, prefix: str):
	rep_root = stage.GetPrimAtPath("/Replicator")
	if not rep_root or not rep_root.IsValid():
		return None

	best = None
	best_n = -1

	for prim in Usd.PrimRange(rep_root):
		if not prim.IsA(UsdGeom.Xform):
			continue

		name = prim.GetName()
		if name == prefix:
			n = 0
		else:
			m = re.match(rf"^{re.escape(prefix)}_(\d+)$", name)
			if not m:
				continue
			n = int(m.group(1))

		if n >= best_n:
			best_n = n
			best = prim

	return best


def _find_first_mesh_child(xform_prim):
	if not xform_prim or not xform_prim.IsValid():
		return None

	for prim in Usd.PrimRange(xform_prim):
		if prim.IsA(UsdGeom.Mesh):
			return prim
	return None


class SyntheticCaptureScenario:
	def __init__(self):
		self._created = False
		self._camera = None
		self._render_product = None
		self._prims = {}

	async def flush(self):
		await omni.kit.app.get_app().next_update_async()
		# flush replicator SDG
		await rep.orchestrator.step_async()

	def create_scene(self, cone_pos, sphere_pos, cube_pos, cam_pos, lookat):
		self.reset()
		os.makedirs(gv.OUTPUT_DIR, exist_ok=True)

		carb.log_info(
			f"[Scenario] create_scene cone={cone_pos} sphere={sphere_pos} cube={cube_pos} cam={cam_pos} lookat={lookat}"
		)

		def _place_on_ground(pos, half_height):
			x, y, _ = float(pos[0]), float(pos[1]), float(pos[2])
			return [x, y, 0.0 + half_height]

		obj_scale = [0.25, 0.25, 0.25]
		half_h = 0.5 * obj_scale[2]

		cone_position = _place_on_ground(cone_pos, half_height=half_h)
		sphere_position = _place_on_ground(sphere_pos, half_height=half_h)
		cube_position = _place_on_ground(cube_pos, half_height=half_h)

		# IMPORTANT: layer name fixed -> Replicator clears it on each call
		# If a layer of the same name already exists, it is cleared before new changes are applied. :contentReference[oaicite:3]{index=3}
		with rep.new_layer("Replicator"):
			ground = rep.create.plane(position=[0, 0, 0], rotation=[0, 0, 0], scale=[20, 20, 1])

			cone = rep.create.cone(position=cone_position, scale=obj_scale)
			sphere = rep.create.sphere(position=sphere_position, scale=obj_scale)
			cube = rep.create.cube(position=cube_position, scale=obj_scale)

			self._camera = rep.create.camera(position=list(cam_pos), look_at=list(lookat))
			self._render_product = rep.create.render_product(self._camera, resolution=gv.RESOLUTION)

		# Collision (optional)
		try:
			stage = omni.usd.get_context().get_stage()
			plane_xf = _find_xform_by_prefix_latest(stage, "Plane_Xform")
			plane_mesh = _find_first_mesh_child(plane_xf)
			target = plane_xf if (plane_xf and plane_xf.IsValid()) else plane_mesh

			if target and target.IsValid():
				UsdPhysics.CollisionAPI.Apply(target)
				PhysxSchema.PhysxCollisionAPI.Apply(target)
		except Exception as e:
			carb.log_warn(f"[Scenario] Could not apply physics collision to ground: {e}")

		# Semantics on Mesh prims
		stage = omni.usd.get_context().get_stage()

		cone_mesh = _find_first_mesh_child(_find_xform_by_prefix_latest(stage, "Cone_Xform"))
		sphere_mesh = _find_first_mesh_child(_find_xform_by_prefix_latest(stage, "Sphere_Xform"))
		cube_mesh = _find_first_mesh_child(_find_xform_by_prefix_latest(stage, "Cube_Xform"))
		plane_mesh = _find_first_mesh_child(_find_xform_by_prefix_latest(stage, "Plane_Xform"))

		def _tag(mesh_prim, label):
			if not mesh_prim or not mesh_prim.IsValid():
				carb.log_warn(f"[Scenario] Mesh not found for label={label}")
				return
			with rep.get.prims(path_pattern=str(mesh_prim.GetPath())):
				rep.modify.semantics([("class", label)])

		_tag(cone_mesh, "cone")
		_tag(sphere_mesh, "sphere")
		_tag(cube_mesh, "cube")
		_tag(plane_mesh, "ground")

		self._prims = {"ground": ground, "cone": cone, "sphere": sphere, "cube": cube}
		self._created = True
		return True

	async def generate_one_frame_async(self):
		if not self._created or self._render_product is None:
			carb.log_warn("[Scenario] generate_one_frame called before create_scene")
			return False

		# 1 tick UI + 1 step replicator (async)
		await omni.kit.app.get_app().next_update_async()
		await rep.orchestrator.step_async()
		return True

	def reset(self):
		try:
			rep.orchestrator.stop()
		except Exception:
			pass

		self._created = False
		self._camera = None
		self._render_product = None
		self._prims = {}