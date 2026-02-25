import omni.ui as ui
import carb
import omni.timeline
from omni.usd import StageEventType
import asyncio
from . import global_variables as gv
from .scenario import SyntheticCaptureScenario


class UIBuilder:
	def __init__(self):
		self._scenario = SyntheticCaptureScenario()
		self._status_label = None

		self._timeline = omni.timeline.get_timeline_interface()

	def on_menu_callback(self):
		pass

	def on_timeline_event(self, event):
		# No fem res especial per ara
		pass

	def on_physics_step(self, step: float):
		# No fem res: la nostra extensió no depèn del physics step
		pass

	def on_stage_event(self, event):
		# Si l'usuari obre un stage nou, pots decidir si resetejar estat intern
		if event.type == int(StageEventType.OPENED):
			self._scenario.reset()
			if self._status_label:
				self._status_label.text = "Status: stage opened -> reset."

	def cleanup(self):
		# Ara no tenim wrapped_ui_elements, així que cleanup és trivial
		pass

	def build_ui(self):
		self._cone_m = [ui.SimpleFloatModel(v) for v in gv.DEFAULT_CONE_POS]
		self._sphere_m = [ui.SimpleFloatModel(v) for v in gv.DEFAULT_SPHERE_POS]
		self._cube_m = [ui.SimpleFloatModel(v) for v in gv.DEFAULT_CUBE_POS]
		self._cam_m = [ui.SimpleFloatModel(v) for v in gv.DEFAULT_CAM_POS]
		self._look_m = [ui.SimpleFloatModel(v) for v in gv.DEFAULT_LOOKAT]

		with ui.VStack(spacing=10, height=0):
			ui.Label("Synthetic Capture", style={"font_size": 18})

			ui.Label("Object positions (m)")
			self._vec3_row("Cone", self._cone_m)
			self._vec3_row("Sphere", self._sphere_m)
			self._vec3_row("Cube", self._cube_m)

			ui.Separator()

			ui.Label("Camera")
			self._vec3_row("Camera pos", self._cam_m)
			self._vec3_row("Look-at", self._look_m)

			ui.Separator()

			with ui.HStack(spacing=10):
				ui.Button("Create Scene", clicked_fn=self._on_create_scene)
				ui.Button("Start (Generate 1 frame)", clicked_fn=self._on_start)

			ui.Button("Reset", clicked_fn=self._on_reset)

			self._status_label = ui.Label("Status: idle")

	def _vec3_row(self, name, models):
		with ui.HStack(spacing=8, height=0):
			ui.Label(name, width=110)
			for m in models:
				ui.FloatField(model=m, width=90)

	def _get_vec3(self, models):
		return (float(models[0].as_float), float(models[1].as_float), float(models[2].as_float))

	def _on_create_scene(self):
		cone = self._get_vec3(self._cone_m)
		sphere = self._get_vec3(self._sphere_m)
		cube = self._get_vec3(self._cube_m)
		cam = self._get_vec3(self._cam_m)
		look = self._get_vec3(self._look_m)

		self._status_label.text = "Status: creating scene..."
		self._scenario.create_scene(cone, sphere, cube, cam, look)
		asyncio.ensure_future(self._scenario.flush())
		self._status_label.text = "Status: scene created."

	def _on_start(self):
		self._status_label.text = "Status: generating..."
		asyncio.ensure_future(self._start_async())

	async def _start_async(self):
		ok = await self._scenario.generate_one_frame_async()
		self._status_label.text = "Status: done." if ok else "Status: failed (see Console)."

	def _on_reset(self):
		self._scenario.reset()
		self._status_label.text = "Status: reset."