# Copyright (c) 2022-2024, NVIDIA CORPORATION. All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto. Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.
#

EXTENSION_TITLE = "Primera_Extensio"

EXTENSION_DESCRIPTION = "Generar_Imagen_Etiquetada"

OUTPUT_DIR = "/home/beamagine/Documentos/Uri/Imatges_generades"
RESOLUTION = (1280, 720)

DEFAULT_CONE_POS = (0.0, 0.0, 0.0)
DEFAULT_SPHERE_POS = (0.5, 0.0, 0.0)
DEFAULT_CUBE_POS = (-0.5, 0.0, 0.0)

DEFAULT_CAM_POS = (1.5, 1.0, 1.5)
DEFAULT_LOOKAT = (0.0, 0.0, 0.0)

CLASS_TO_COLOR = {
	"cone": (255, 0, 0),
	"sphere": (0, 255, 0),
	"cube": (0, 0, 255),
}
