import os
import carb
import re
import omni.kit.app
import omni.replicator.core as rep
import omni.usd
import json
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema

from . import global_variables as gv


# -------------
# Helpers USD
# -------------
def _find_xform_by_prefix_latest(stage, prefix: str):
	''' Busca el prim Xform més nou '''
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
	''' Donat un XForm busca el primer fill '''
	if not xform_prim or not xform_prim.IsValid():
		return None

	for prim in Usd.PrimRange(xform_prim):
		if prim.IsA(UsdGeom.Mesh):
			return prim
	return None


def _json_load_if_str(x):
	if isinstance(x, str):
		try:
			return json.loads(x)
		except Exception:
			return x
	return x


def _to_jsonable(obj):
	if isinstance(obj, np.ndarray):
		return obj.tolist()
	if isinstance(obj, np.generic):
		return obj.item()
	if isinstance(obj, dict):
		return {str(k): _to_jsonable(v) for k, v in obj.items()}
	if isinstance(obj, (list, tuple)):
		return [_to_jsonable(v) for v in obj]
	return obj


# -------------------------
# Scenario
# -------------------------
class SyntheticCaptureScenario:
	def __init__(self):
		self._created = False
		self._camera = None
		self._render_product = None
		self._prims = {}
		self._frame_idx = 0

		self._ann_rgb = None # (LdrColor)
		self._ann_sem = None # (semantic_segmentation)
		self._ann_bbox = None # (bounding_box_2d_tight)

		self._draw_ground_label = False

	async def flush(self):
		await omni.kit.app.get_app().next_update_async()
		await rep.orchestrator.step_async()

	def reset(self):
		try:
			rep.orchestrator.stop()
		except Exception:
			pass

		self._created = False
		self._camera = None
		self._render_product = None
		self._prims = {}

	def create_scene(self, cone_pos, sphere_pos, cube_pos, cam_pos, lookat):
		self.reset()
		os.makedirs(gv.OUTPUT_DIR, exist_ok=True)

		def _place_on_ground(pos, half_height):
			x, y, _ = float(pos[0]), float(pos[1]), float(pos[2])
			return [x, y, 0.0 + half_height]

		obj_scale = [0.25, 0.25, 0.25]
		half_h = 0.5 * obj_scale[2]

		cone_position = _place_on_ground(cone_pos, half_height=half_h)
		sphere_position = _place_on_ground(sphere_pos, half_height=half_h)
		cube_position = _place_on_ground(cube_pos, half_height=half_h)

		with rep.new_layer("Replicator"):
			ground = rep.create.plane(position=[0, 0, 0], rotation=[0, 0, 0], scale=[20, 20, 1])
			cone = rep.create.cone(position=cone_position, scale=obj_scale)
			sphere = rep.create.sphere(position=sphere_position, scale=obj_scale)
			cube = rep.create.cube(position=cube_position, scale=obj_scale)

			# Assignem semantica
			with ground:
				rep.modify.semantics([("class", "ground")])
			with cone:
				rep.modify.semantics([("class", "cone")])
			with sphere:
				rep.modify.semantics([("class", "sphere")])
			with cube:
				rep.modify.semantics([("class", "cube")])

			self._camera = rep.create.camera(position=list(cam_pos), look_at=list(lookat))
			self._render_product = rep.create.render_product(self._camera, resolution=gv.RESOLUTION)

			# Assignem Annotators
			self._ann_rgb = rep.AnnotatorRegistry.get_annotator("LdrColor")
			self._ann_rgb.attach([self._render_product])

			self._ann_sem = rep.AnnotatorRegistry.get_annotator("semantic_segmentation",init_params={"semanticTypes": ["class"], "colorize": False},)
			self._ann_sem.attach([self._render_product])

			self._ann_bbox = rep.AnnotatorRegistry.get_annotator("bounding_box_2d_tight",init_params={"semanticTypes": ["class"]},)
			self._ann_bbox.attach([self._render_product])

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

		self._prims = {"ground": ground, "cone": cone, "sphere": sphere, "cube": cube}
		self._created = True
		return True

	async def generate_one_frame_async(self):
		if not self._created or self._render_product is None:
			carb.log_warn("[Scenario] generate_one_frame called before create_scene")
			return False

		app = omni.kit.app.get_app()
		await app.next_update_async()
		await rep.orchestrator.step_async()
		await app.next_update_async()
		await rep.orchestrator.step_async()

		# Llegim els annotators
		try:
			rgb_raw = self._ann_rgb.get_data()
			sem_raw = self._ann_sem.get_data()
			bbox_raw = self._ann_bbox.get_data()
		except Exception as e:
			carb.log_error(f"[Scenario] Annotator get_data() failed: {e}")
			return False

		# -------------------------
		# Normalize RGB
		# -------------------------
		rgb = np.asarray(rgb_raw)
		if rgb.ndim != 3 or rgb.shape[2] not in (3, 4):
			carb.log_error(f"[Scenario] Unexpected RGB shape={getattr(rgb, 'shape', None)} type={type(rgb_raw)}")
			return False

		res_w, res_h = int(gv.RESOLUTION[0]), int(gv.RESOLUTION[1])
		if rgb.shape[0] == res_w and rgb.shape[1] == res_h:
			rgb = np.transpose(rgb, (1, 0, 2))

		rgb_rgb = rgb[:, :, :3].astype(np.uint8, copy=True)
		H, W = rgb_rgb.shape[0], rgb_rgb.shape[1]

		# -------------------------
		# Normalize semantic ids + idToLabels
		# -------------------------
		sem_ids = None
		id_to_labels_sem = None
		if isinstance(sem_raw, dict):
			sem_ids = sem_raw.get("data")
			info = sem_raw.get("info", {})
			id_to_labels_sem = info.get("idToLabels")
		else:
			sem_ids = sem_raw

		if isinstance(id_to_labels_sem, str):
			try:
				id_to_labels_sem = json.loads(id_to_labels_sem)
			except Exception:
				id_to_labels_sem = None

		if sem_ids is None:
			carb.log_warn("[Scenario] semantic_segmentation returned no data; mask will be empty")
			sem_ids = np.zeros((H, W), dtype=np.int32)

		sem_ids = np.asarray(sem_ids)
		if sem_ids.ndim != 2:
			if sem_ids.ndim == 3 and sem_ids.shape[2] == 1:
				sem_ids = sem_ids[:, :, 0]
			else:
				carb.log_warn(f"[Scenario] Unexpected semantic ids shape={sem_ids.shape}; mask will be empty")
				sem_ids = np.zeros((H, W), dtype=np.int32)

		if sem_ids.shape[0] == res_w and sem_ids.shape[1] == res_h:
			sem_ids = np.transpose(sem_ids, (1, 0))

		mask = np.zeros((sem_ids.shape[0], sem_ids.shape[1], 3), dtype=np.uint8)
		if isinstance(id_to_labels_sem, dict):
			for sid_key, label_info in id_to_labels_sem.items():
				try:
					sid = int(sid_key)
				except Exception:
					continue

				label = None
				if isinstance(label_info, dict):
					label = label_info.get("class")
					if label is None and isinstance(label_info.get("labels"), dict):
						label = label_info["labels"].get("class")

				if not label or label not in gv.CLASS_TO_COLOR:
					continue
				mask[sem_ids == sid] = np.asarray(gv.CLASS_TO_COLOR[label], dtype=np.uint8)
		else:
			carb.log_warn("[Scenario] semantic_segmentation idToLabels missing; mask will be empty")

		alpha = 0.45
		overlay_np = ((1.0 - alpha) * rgb_rgb.astype(np.float32) + alpha * mask.astype(np.float32)).clip(0, 255).astype(
			np.uint8
		)

		# -------------------------
		# Normalize bbox rows + idToLabels
		# -------------------------
		bbox_rows = []
		bbox_info = {}
		if isinstance(bbox_raw, dict):
			bbox_data = bbox_raw.get("data")
			bbox_info = bbox_raw.get("info", {}) if isinstance(bbox_raw.get("info", {}), dict) else {}
		else:
			bbox_data = bbox_raw

		def _is_scalar(x):
			return isinstance(x, (int, float, np.integer, np.floating))

		# Normalize bbox_data -> list[rows]
		if bbox_data is None:
			bbox_rows = []
		elif isinstance(bbox_data, np.ndarray):
			if bbox_data.size == 0:
				bbox_rows = []
			else:
				if bbox_data.dtype.fields is not None:
					bbox_rows = bbox_data.tolist()
				elif bbox_data.ndim == 1:
					first = bbox_data[0]
					if _is_scalar(first):
						bbox_rows = [bbox_data.tolist()]
					else:
						bbox_rows = bbox_data.tolist()
				else:
					bbox_rows = bbox_data.tolist()
		elif isinstance(bbox_data, list):
			if len(bbox_data) == 0:
				bbox_rows = []
			else:
				if not isinstance(bbox_data[0], (list, tuple, dict)):
					bbox_rows = [bbox_data]
				else:
					bbox_rows = bbox_data
		else:
			bbox_rows = []

		# idToLabels del bbox (pot faltar o venir com string JSON)
		id_to_labels_bbox = None
		if isinstance(bbox_info, dict):
			id_to_labels_bbox = bbox_info.get("idToLabels")
		if id_to_labels_bbox is None and isinstance(bbox_raw, dict):
			id_to_labels_bbox = bbox_raw.get("idToLabels")

		if isinstance(id_to_labels_bbox, str):
			try:
				id_to_labels_bbox = json.loads(id_to_labels_bbox)
			except Exception:
				id_to_labels_bbox = None

		if not isinstance(id_to_labels_bbox, dict):
			id_to_labels_bbox = {}

		def _extract_class(label_info):
			if label_info is None:
				return None
			if isinstance(label_info, str):
				return label_info
			if not isinstance(label_info, dict):
				return None

			c = label_info.get("class")
			if isinstance(c, str) and c:
				return c

			labels = label_info.get("labels")
			if isinstance(labels, dict):
				c2 = labels.get("class")
				if isinstance(c2, str) and c2:
					return c2

			return None

		def _resolve_label(semantic_id):
			li = id_to_labels_bbox.get(str(semantic_id), id_to_labels_bbox.get(int(semantic_id), None))
			lbl = _extract_class(li)
			if lbl:
				return lbl

			if isinstance(id_to_labels_sem, dict):
				li2 = id_to_labels_sem.get(str(semantic_id), id_to_labels_sem.get(int(semantic_id), None))
				lbl2 = _extract_class(li2)
				if lbl2:
					return lbl2
			return None

		def _to_pixel_coords(xmin, ymin, xmax, ymax, W, H):
			if 0.0 <= xmax <= 1.5 and 0.0 <= ymax <= 1.5 and 0.0 <= xmin <= 1.5 and 0.0 <= ymin <= 1.5:
				xmin *= W
				xmax *= W
				ymin *= H
				ymax *= H
			return xmin, ymin, xmax, ymax

		# -------------------------
		# Draw debug + labels (PIL) and SAVE PIL image
		# -------------------------
		idx = int(self._frame_idx)
		self._frame_idx += 1

		overlay_img = Image.fromarray(overlay_np)
		draw = ImageDraw.Draw(overlay_img)
		try:
			font = ImageFont.truetype("DejaVuSans.ttf", 20)
		except Exception:
			font = ImageFont.load_default()

		padding_y = 10
		drawn = 0

		for row in bbox_rows:
			if isinstance(row, dict):
				bid = int(row.get("semanticId", row.get("id", -1)))
				xmin = float(row.get("x_min", row.get("xmin", 0)))
				ymin = float(row.get("y_min", row.get("ymin", 0)))
				xmax = float(row.get("x_max", row.get("xmax", 0)))
				ymax = float(row.get("y_max", row.get("ymax", 0)))
			else:
				row = list(row) if isinstance(row, (list, tuple)) else [row]
				if len(row) < 5:
					continue
				bid = int(row[0])
				xmin, ymin, xmax, ymax = [float(v) for v in row[1:5]]

			xmin, ymin, xmax, ymax = _to_pixel_coords(xmin, ymin, xmax, ymax, W, H)

			xmin_i, xmax_i = sorted((max(0, int(xmin)), min(W - 1, int(xmax))))
			ymin_i, ymax_i = sorted((max(0, int(ymin)), min(H - 1, int(ymax))))

			label = _resolve_label(bid)
			carb.log_info(f"[Scenario] bbox id={bid} rect=({xmin_i},{ymin_i})-({xmax_i},{ymax_i}) label={label}")

			if not label:
				draw.rectangle([xmin_i, ymin_i, xmax_i, ymax_i], outline=(255, 255, 255), width=1)
				continue

			if label == "ground" and not getattr(self, "_draw_ground_label", False):
				continue

			draw.rectangle([xmin_i, ymin_i, xmax_i, ymax_i], outline=(255, 255, 255), width=1)

			text = str(label)
			tb = draw.textbbox((0, 0), text, font=font)
			tw, th = tb[2] - tb[0], tb[3] - tb[1]

			tx = (xmin_i + xmax_i) // 2
			ty = ymax_i + padding_y

			x0 = max(0, min(W - tw - 1, tx - tw // 2))
			y0 = max(0, min(H - th - 1, ty))

			carb.log_info(f"[Scenario] draw label '{text}' at ({x0},{y0})")

			for ox, oy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
				draw.text((x0 + ox, y0 + oy), text, fill=(0, 0, 0), font=font)
			draw.text((x0, y0), text, fill=(255, 255, 255), font=font)
			drawn += 1

		# -------------------------
		# Save outputs (ensure PIL image with text is saved)
		# -------------------------
		os.makedirs(gv.OUTPUT_DIR, exist_ok=True)
		rgb_path = os.path.join(gv.OUTPUT_DIR, f"rgb_{idx:04d}.png")
		mask_path = os.path.join(gv.OUTPUT_DIR, f"mask_{idx:04d}.png")
		overlay_path = os.path.join(gv.OUTPUT_DIR, f"overlay_{idx:04d}.png")
		bbox_path = os.path.join(gv.OUTPUT_DIR, f"bboxes_{idx:04d}.json")

		Image.fromarray(rgb_rgb).save(rgb_path)
		Image.fromarray(mask).save(mask_path)
		overlay_img.save(overlay_path)

		def _to_jsonable(obj):
			if isinstance(obj, np.ndarray):
				return obj.tolist()
			if isinstance(obj, np.generic):
				return obj.item()
			if isinstance(obj, dict):
				return {str(k): _to_jsonable(v) for k, v in obj.items()}
			if isinstance(obj, (list, tuple)):
				return [_to_jsonable(v) for v in obj]
			return obj

		out = {
			"frame": int(idx),
			"resolution": [int(W), int(H)],
			"bbox": bbox_raw,
			"idToLabels_semantic": id_to_labels_sem,
		}

		with open(bbox_path, "w", encoding="utf-8") as f:
			json.dump(_to_jsonable(out), f, indent=2)

		#carb.log_info(f"[Scenario] Saved rgb={rgb_path}")
		#carb.log_info(f"[Scenario] Saved mask={mask_path}")
		#carb.log_info(f"[Scenario] Saved overlay(with text)={overlay_path}")
		#carb.log_info(f"[Scenario] Saved bbox={bbox_path}")
		#carb.log_info(f"[Scenario] Labels drawn (excluding ground unless enabled) = {drawn}")
		return True