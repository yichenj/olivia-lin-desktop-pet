"""Render a gentle animated 2.5D portrait loop with Blender's Eevee renderer.

The generated illustration is placed on a transparent, emissive camera-facing plane.
This creates a lightweight idle-motion sprite; it is not a rigged 3D likeness.
"""
import argparse
import math
import os
import sys

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--out", required=True)
parser.add_argument("--blend-out", default="")
parser.add_argument("--save-only", action="store_true")
args = parser.parse_args(argv)

import bpy

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE"
scene.eevee.taa_render_samples = 8
scene.render.resolution_x = 480
scene.render.resolution_y = 640
scene.render.resolution_percentage = 100
scene.render.film_transparent = True
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.image_settings.color_depth = "8"
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "Medium High Contrast"

world = bpy.data.worlds.new("Transparent World") if not bpy.data.worlds else bpy.data.worlds[0]
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.0, 0.0, 0.0, 0.0)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0
scene.world = world

# A front-facing orthographic camera keeps the portrait readable as a desktop sprite.
bpy.ops.object.camera_add(location=(0.0, 0.0, 5.0))
cam = bpy.context.object
cam.name = "Portrait Camera"
cam.data.type = "ORTHO"
cam.data.ortho_scale = 4.45
scene.camera = cam
cam.rotation_euler = (0, 0, 0)

img = bpy.data.images.load(os.path.abspath(args.input), check_existing=True)
img.pack()
# The supplied generated portrait is 3:4. This plane preserves the image ratio.
bpy.ops.mesh.primitive_plane_add(size=2, location=(0, 0, 0))
plane = bpy.context.object
plane.name = "Olivia portrait billboard"
plane.scale = (1.36, 1.815, 1.0)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

mat = bpy.data.materials.new("Portrait with native alpha")
mat.use_nodes = True
nodes = mat.node_tree.nodes
nodes.clear()
tex = nodes.new("ShaderNodeTexImage")
tex.image = img
tex.interpolation = "Linear"
tex.extension = "CLIP"
transparent = nodes.new("ShaderNodeBsdfTransparent")
em = nodes.new("ShaderNodeEmission")
em.inputs["Strength"].default_value = 1.0
mix = nodes.new("ShaderNodeMixShader")
out = nodes.new("ShaderNodeOutputMaterial")
mat.node_tree.links.new(tex.outputs["Color"], em.inputs["Color"])
mat.node_tree.links.new(tex.outputs["Alpha"], mix.inputs["Fac"])
mat.node_tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
mat.node_tree.links.new(em.outputs["Emission"], mix.inputs[2])
mat.node_tree.links.new(mix.outputs["Shader"], out.inputs["Surface"])
try:
    mat.blend_method = "BLEND"
    mat.show_transparent_back = True
except AttributeError:
    pass
plane.data.materials.append(mat)

# Animate a barely-there float and sway. The Qt companion provides interactions.
plane.keyframe_insert(data_path="location", frame=1)
plane.keyframe_insert(data_path="rotation_euler", frame=1)
plane.location.y = 0.045
plane.rotation_euler.z = math.radians(0.55)
plane.keyframe_insert(data_path="location", frame=13)
plane.keyframe_insert(data_path="rotation_euler", frame=13)
plane.location.y = 0.0
plane.rotation_euler.z = 0.0
plane.keyframe_insert(data_path="location", frame=25)
plane.keyframe_insert(data_path="rotation_euler", frame=25)
plane.location.y = -0.045
plane.rotation_euler.z = math.radians(-0.55)
plane.keyframe_insert(data_path="location", frame=37)
plane.keyframe_insert(data_path="rotation_euler", frame=37)
plane.location.y = 0.0
plane.rotation_euler.z = 0.0
plane.keyframe_insert(data_path="location", frame=49)
plane.keyframe_insert(data_path="rotation_euler", frame=49)
for curve in plane.animation_data.action.fcurves:
    for key in curve.keyframe_points:
        key.interpolation = "BEZIER"
scene.frame_start = 1
scene.frame_end = 48
scene.render.fps = 12
os.makedirs(args.out, exist_ok=True)
scene.render.filepath = os.path.join(os.path.abspath(args.out), "frame_")
scene.render.image_settings.color_mode = "RGBA"

if args.blend_out:
    blend_path = os.path.abspath(args.blend_out)
    os.makedirs(os.path.dirname(blend_path), exist_ok=True)
    scene.frame_set(scene.frame_start)
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"Saved editable Blender scene to {blend_path}")
if args.save_only:
    sys.exit(0)

for frame in range(scene.frame_start, scene.frame_end + 1):
    scene.frame_set(frame)
    scene.render.filepath = os.path.join(os.path.abspath(args.out), f"frame_{frame:03d}.png")
    bpy.ops.render.render(write_still=True)
print(f"Rendered {scene.frame_end} transparent frames to {os.path.abspath(args.out)}")
