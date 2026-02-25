# Copyright (c) 2022-2024, NVIDIA CORPORATION. All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto. Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

import asyncio
import gc
import carb
import omni
import omni.kit.commands
import omni.physx as _physx
import omni.timeline
import omni.ui as ui
import omni.usd
from isaacsim.gui.components.element_wrappers import ScrollingWindow
from isaacsim.gui.components.menu import MenuItemDescription
from omni.kit.menu.utils import add_menu_items, remove_menu_items
from omni.usd import StageEventType

from .global_variables import EXTENSION_DESCRIPTION, EXTENSION_TITLE
from .ui_builder import UIBuilder

"""
This file serves as a basic template for the standard boilerplate operations
that make a UI-based extension appear on the toolbar.

This implementation is meant to cover most use-cases without modification.
Various callbacks are hooked up to a seperate class UIBuilder in .ui_builder.py
Most users will be able to make their desired UI extension by interacting solely with
UIBuilder.

This class sets up standard useful callback functions in UIBuilder:
	on_menu_callback: Called when extension is opened
	on_timeline_event: Called when timeline is stopped, paused, or played
	on_physics_step: Called on every physics step
	on_stage_event: Called when stage is opened or closed
	cleanup: Called when resources such as physics subscriptions should be cleaned up
	build_ui: User function that creates the UI they want.
"""


class Extension(omni.ext.IExt):
	def on_startup(self, ext_id: str):
		"""Initialize extension and UI elements"""
		carb.log_info("[Primera_Extensio] on_startup begin")

		self.ext_id = ext_id
		self._usd_context = omni.usd.get_context()
		self._task = None
		self._timeline_event_sub = None

		# Build Window
		self._window = ScrollingWindow(
			title=EXTENSION_TITLE, width=600, height=500, visible=False, dockPreference=ui.DockPreference.LEFT_BOTTOM
		)
		self._window.set_visibility_changed_fn(self._on_window)

		action_registry = omni.kit.actions.core.get_action_registry()
		action_registry.register_action(
			ext_id,
			f"CreateUIExtension:{EXTENSION_TITLE}",
			self._menu_callback,
			description=f"Add {EXTENSION_TITLE} Extension to UI toolbar",
		)
		self._menu_items = [
			MenuItemDescription(name=EXTENSION_TITLE, onclick_action=(ext_id, f"CreateUIExtension:{EXTENSION_TITLE}"))
		]

		add_menu_items(self._menu_items, EXTENSION_TITLE)

		# Filled in with User Functions
		self.ui_builder = UIBuilder()

		# Events
		self._usd_context = omni.usd.get_context()
		self._physxIFace = _physx.acquire_physx_interface()
		self._physx_subscription = None
		self._stage_event_sub = None
		self._timeline = omni.timeline.get_timeline_interface()
		carb.log_info("[Primera_Extensio] on_startup end")

	def on_shutdown(self):
		 # Cancel task (dock coroutine) if pending
		try:
			if getattr(self, "_task", None) is not None:
				self._task.cancel()
		except Exception:
			pass
		self._task = None

		# Unsubscribe streams before destroying UI
		self._stage_event_sub = None
		self._timeline_event_sub = None
		self._physx_subscription = None

		# 1) Elimina menú primer
		try:
			remove_menu_items(self._menu_items, EXTENSION_TITLE)
		except Exception:
			pass
		# 2) Elimina acció
		try:
			action_registry = omni.kit.actions.core.get_action_registry()
			action_registry.deregister_action(self.ext_id, f"CreateUIExtension:{EXTENSION_TITLE}")
		except Exception:
			pass
		# 3) Destrueix finestra
		w = getattr(self, "_window", None)
		if w is not None:
			try:
				w.visible = False
				w.destroy()
			except Exception:
				pass
		self._window = None
		# 4) Cleanup UI builder
		try:
			self.ui_builder.cleanup()
		except Exception:
			pass
		gc.collect()

	def _on_window(self, visible):
		# Guard against late callbacks after shutdown/destroy
		w = getattr(self, "_window", None)
		if w is None:
			return

		if w.visible:
			# Subscribe to Stage and Timeline Events
			self._usd_context = omni.usd.get_context()
			events = self._usd_context.get_stage_event_stream()
			self._stage_event_sub = events.create_subscription_to_pop(self._on_stage_event)
			stream = self._timeline.get_timeline_event_stream()
			self._timeline_event_sub = stream.create_subscription_to_pop(self._on_timeline_event)

			self._build_ui()
		else:
			self._usd_context = None
			self._stage_event_sub = None
			self._timeline_event_sub = None
			self.ui_builder.cleanup()

	def _build_ui(self):
		with self._window.frame:
			with ui.VStack(spacing=5, height=0):
				self._build_extension_ui()

		async def dock_window():
			await omni.kit.app.get_app().next_update_async()

			def dock(space, name, location, pos=0.5):
				window = omni.ui.Workspace.get_window(name)
				if window and space:
					window.dock_in(space, location, pos)
				return window

			tgt = ui.Workspace.get_window("Viewport")
			dock(tgt, EXTENSION_TITLE, omni.ui.DockPosition.LEFT, 0.33)
			await omni.kit.app.get_app().next_update_async()

		self._task = asyncio.ensure_future(dock_window())

	#################################################################
	# Functions below this point call user functions
	#################################################################

	def _menu_callback(self):
		# Window can be None if it was destroyed or shutdown already ran.
		w = getattr(self, "_window", None)
		if w is None:
			carb.log_warn("[Primera_Extensio] _window is None; ignoring menu action (extension not ready or already shutdown)")
			return

		# Toggle safely
		try:
			w.visible = not bool(w.visible)
		except Exception as e:
			carb.log_error(f"[Primera_Extensio] Failed toggling window visibility: {e}")
			return

		# Callback UI (optional)
		try:
			self.ui_builder.on_menu_callback()
		except Exception as e:
			carb.log_error(f"[Primera_Extensio] ui_builder.on_menu_callback error: {e}")

	def _on_timeline_event(self, event):
		if event.type == int(omni.timeline.TimelineEventType.PLAY):
			if not self._physx_subscription:
				self._physx_subscription = self._physxIFace.subscribe_physics_step_events(self._on_physics_step)
		elif event.type == int(omni.timeline.TimelineEventType.STOP):
			self._physx_subscription = None

		self.ui_builder.on_timeline_event(event)

	def _on_physics_step(self, step):
		self.ui_builder.on_physics_step(step)

	def _on_stage_event(self, event):
		if event.type == int(StageEventType.OPENED) or event.type == int(StageEventType.CLOSED):
			# stage was opened or closed, cleanup
			self._physx_subscription = None
			self.ui_builder.cleanup()

		self.ui_builder.on_stage_event(event)

	def _build_extension_ui(self):
		# Call user function for building UI
		self.ui_builder.build_ui()
