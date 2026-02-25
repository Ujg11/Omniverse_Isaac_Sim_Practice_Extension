import os
import carb
import re

import omni.replicator.core as rep
import omni.kit.commands

from . import global_variables as gv
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema


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
	"""Retorna el primer Mesh dins d’un Xform (descendent)."""
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

	def create_scene(self, cone_pos, sphere_pos, cube_pos, cam_pos, lookat):
		os.makedirs(gv.OUTPUT_DIR, exist_ok=True)

		carb.log_info(
			f"[Scenario] create_scene cone={cone_pos} sphere={sphere_pos} cube={cube_pos} cam={cam_pos} lookat={lookat}"
		)

		# Crea una layer Replicator (aïlla els prims que creem)
		rep.new_layer()

		# --- Ground (sin depender de comandos) ---
		# Creamos un plano visible + le añadimos colisión PhysX para que actúe como suelo.
		# Nota: esto funciona aunque el comando CreateGroundPlane no exista en tu versión.
		ground = rep.create.plane(position=[0,0,0], rotation=[0,0,0], scale=[20,20,1])

		# Intentamos añadir colisión al prim del suelo si las APIs USD/PhysX están disponibles.
		try:
			stage = omni.usd.get_context().get_stage()
			plane_xf = _find_xform_by_prefix(stage, "Plane_Xform")
			plane_mesh = _find_first_mesh_child(plane_xf)
			if plane_mesh and plane_mesh.IsValid():
				UsdPhysics.CollisionAPI.Apply(plane_mesh)
				PhysxSchema.PhysxCollisionAPI.Apply(plane_mesh)
			else:
				carb.log_warn("[Scenario] Could not find Plane mesh under /Replicator for collision.")
		except Exception as e:
			carb.log_warn(f"[Scenario] Could not apply physics collision to ground: {e}")

		# Helper: coloca un objeto justo encima del suelo (z=0) según un tamaño aproximado.
		def _place_on_ground(pos, half_height):
			# pos puede ser tupla/lista/numpy; usamos (x,y,z)
			x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
			return [x, y, 0.0 + half_height]

		obj_scale = [0.25, 0.25, 0.25]

		# Ajuste por geometría para que la base quede en z=0.
		# Nota: Replicator define"size" de sus primitivos en unidades USD; con scale=0.25:
		#  - cube: tamaño base ~1 -> altura ~1*scale_z => half-height = 0.5*scale_z
		#  - sphere: radio ~0.5 -> radio escalado = 0.5*scale_z
		#  - cone/cylinder suelen tener altura ~1 -> half-height = 0.5*scale_z
		cube_half_height = 0.5 * obj_scale[2]
		sphere_half_height = 0.5 * obj_scale[2]
		cone_half_height = 0.5 * obj_scale[2]

		cone_position = _place_on_ground(cone_pos, half_height=cone_half_height)
		sphere_position = _place_on_ground(sphere_pos, half_height=sphere_half_height)
		cube_position = _place_on_ground(cube_pos, half_height=cube_half_height)

		# Crear objectes
		cone = rep.create.cone(position=cone_position, scale=obj_scale)
		sphere = rep.create.sphere(position=sphere_position, scale=obj_scale)
		cube = rep.create.cube(position=cube_position, scale=obj_scale)

		# Etiquetes semantics: type="class"
		stage = omni.usd.get_context().get_stage()

		cone_xf = _find_xform_by_prefix_latest(stage, "Cone_Xform")
		sphere_xf = _find_xform_by_prefix_latest(stage, "Sphere_Xform")
		cube_xf = _find_xform_by_prefix_latest(stage, "Cube_Xform")
		plane_xf = _find_xform_by_prefix_latest(stage, "Plane_Xform")

		cone_mesh = _find_first_mesh_child(cone_xf)
		sphere_mesh = _find_first_mesh_child(sphere_xf)
		cube_mesh = _find_first_mesh_child(cube_xf)
		plane_mesh = _find_first_mesh_child(plane_xf)

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

		carb.log_info(f"[Scenario] cone mesh path: {cone_mesh.GetPath() if cone_mesh else None}")
		carb.log_info(f"[Scenario] sphere mesh path: {sphere_mesh.GetPath() if sphere_mesh else None}")
		carb.log_info(f"[Scenario] cube mesh path: {cube_mesh.GetPath() if cube_mesh else None}")
		carb.log_info(f"[Scenario] plane mesh path: {plane_mesh.GetPath() if plane_mesh else None}")

		self._prims = {"ground": ground, "cone": cone, "sphere": sphere, "cube": cube}

		# Crear càmera i render product
		self._camera = rep.create.camera(position=list(cam_pos), look_at=list(lookat))
		self._render_product = rep.create.render_product(self._camera, resolution=gv.RESOLUTION)

		self._created = True
		return True

	def generate_one_frame(self):
		if not self._created or self._render_product is None:
			carb.log_warn("[Scenario] generate_one_frame called before create_scene")
			return False

		# De moment: només comprovem que el step funciona sense capturar encara
		carb.log_info("[Scenario] orchestrator.step() (no capture yet)")
		rep.orchestrator.step()
		return True

	def reset(self):
		carb.log_info("[Scenario] reset")

		# Atura replicator per si hi havia res corrent
		try:
			rep.orchestrator.stop()
		except Exception:
			pass

		# No hi ha una “delete layer” directa simple: en aquest checkpoint només resetejem estat
		self._created = False
		self._camera = None
		self._render_product = None
		self._prims = {}