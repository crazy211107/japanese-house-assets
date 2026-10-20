# -*- coding: utf-8 -*-
"""
春日灯籠（かすがどうろう）— 程序化建模 → GLB
================================================
在无头 Blender（GitHub Actions / blender -b）里运行，产出可直接被 Three.js
GLTFLoader 读取的 asset。

设计约束（来自 3d-japanese-house 的坐标与预算）：
  · 1 unit = 1 m，Z 轴向上，底面贴 z=0，导出时 export_yup 转成 Three 的 Y-up
  · 总高约 1.93 m（真实春日灯笼 1.8~2.0 m）
  · 面数预算 ≤ 12k，单材质（石），风化靠顶点色而不是贴图

为什么要手写这些几何而不是用布尔：
  布尔在无头环境里一旦失败就是静默的坏几何，而且会产生大量三角面。
  这里全部用「基本体 + 倒角」拼出来：石灯笼本来就是由几块石头叠起来的，
  这个结构反而是对的。

用法：
  blender -b --python tools/build_lantern.py -- --out dist
  blender -b --python tools/build_lantern.py -- --out dist --no-draco
"""

import argparse
import math
import os
import sys

import bpy
import bmesh
from mathutils import Vector


# ---------------------------------------------------------------- 基础工具

def clean_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def apply_bevel(obj, width=0.012, segments=2, angle=40.0):
    """石头不会像刀切的一样利，倒角就是「材质语言」的第一步。"""
    mod = obj.modifiers.new(name='Bevel', type='BEVEL')
    mod.width = width
    mod.segments = segments
    mod.limit_method = 'ANGLE'
    mod.angle_limit = math.radians(angle)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)


def box(name, sx, sy, sz, z, x=0.0, y=0.0, bevel=0.012, taper=1.0):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x, y, z))
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (sx, sy, sz)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    # 上小下大：日本的石构件几乎都有收分（taper），差 2~6% 就很不一样
    if abs(taper - 1.0) > 1e-4:
        me = obj.data
        top = max(v.co.z for v in me.vertices)
        for v in me.vertices:
            if abs(v.co.z - top) < 1e-5:
                v.co.x *= taper
                v.co.y *= taper
        me.update()

    if bevel > 0:
        apply_bevel(obj, width=bevel)
    return obj


def cylinder(name, r_bottom, r_top, height, z, segments=24, x=0.0, y=0.0):
    bpy.ops.mesh.primitive_cone_add(
        vertices=segments, radius1=r_bottom, radius2=r_top,
        depth=height, location=(x, y, z),
    )
    obj = bpy.context.active_object
    obj.name = name
    return obj


# ---------------------------------------------------------------- 石头材质

def make_stone_material():
    mat = bpy.data.materials.new('stone_granite')
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = (0.44, 0.43, 0.40, 1.0)
    bsdf.inputs['Roughness'].default_value = 0.88
    bsdf.inputs['Metallic'].default_value = 0.0

    # 顶点色必须真的接进节点树，否则 glTF 导出器会以
    # "The active Vertex Color will not be exported, as it is not used in
    #  the node tree of the material" 为由整段丢掉 COLOR_0。
    vcol = nt.nodes.new('ShaderNodeVertexColor')
    vcol.layer_name = 'Col'
    vcol.location = (-520, 160)

    mix = nt.nodes.new('ShaderNodeMixRGB')
    mix.blend_type = 'MULTIPLY'
    mix.inputs['Fac'].default_value = 1.0
    mix.inputs['Color1'].default_value = (0.44, 0.43, 0.40, 1.0)
    mix.location = (-260, 160)

    nt.links.new(vcol.outputs['Color'], mix.inputs['Color2'])
    nt.links.new(mix.outputs['Color'], bsdf.inputs['Base Color'])
    return mat


def hash01(x, y, z, seed=0.0):
    """确定性伪随机：同一个位置每次导出颜色一致，不会每次构建都变样。"""
    n = math.sin(x * 12.9898 + y * 78.233 + z * 37.719 + seed) * 43758.5453
    return n - math.floor(n)


def paint_weathering(obj):
    """
    顶点色做三件事：整体明度起伏、底部积灰、朝上的面长苔。
    glTF 的 COLOR_0 会和 baseColor 相乘，所以这里存的是「乘数」而不是颜色。
    """
    me = obj.data
    if not me.color_attributes:
        me.color_attributes.new(name='Col', type='FLOAT_COLOR', domain='CORNER')
    layer = me.color_attributes['Col']

    me.calc_normals_split() if hasattr(me, 'calc_normals_split') else None

    vnormals = {}
    for poly in me.polygons:
        for vi in poly.vertices:
            vnormals.setdefault(vi, Vector((0, 0, 0)))
            vnormals[vi] += poly.normal

    for poly in me.polygons:
        for loop_idx in poly.loop_indices:
            vi = me.loops[loop_idx].vertex_index
            co = me.vertices[vi].co
            n = vnormals.get(vi, Vector((0, 0, 1)))
            n = n.normalized() if n.length > 1e-6 else Vector((0, 0, 1))

            # 1) 轻微的石面明暗（0.86~1.0）
            grain = 0.86 + 0.14 * hash01(co.x * 7.0, co.y * 7.0, co.z * 7.0)

            # 2) 接近地面的部分积灰发暗
            dust = 1.0 - 0.16 * max(0.0, 1.0 - co.z / 0.55)

            # 3) 朝上的水平面积苔（越靠下越重）
            moss_n = hash01(co.x * 2.3, co.y * 2.3, co.z * 0.7, 9.17)
            moss = max(0.0, (n.z - 0.55) / 0.45) * max(0.0, 1.0 - co.z / 1.25)
            moss *= 0.35 + 0.65 * moss_n
            moss = min(0.55, moss)

            r = grain * dust
            g = grain * dust
            b = grain * dust
            # 苔藓把红色压下去、绿色留一点
            r *= (1.0 - 0.45 * moss)
            g *= (1.0 - 0.12 * moss)
            b *= (1.0 - 0.55 * moss)

            layer.data[loop_idx].color = (r, g, b, 1.0)


# ---------------------------------------------------------------- 造型

def build_lantern():
    parts = []

    # 基础（きそ）：两层方石
    parts.append(box('base_lower', 0.66, 0.66, 0.13, z=0.065, bevel=0.014, taper=0.985))
    parts.append(box('base_upper', 0.54, 0.54, 0.11, z=0.185, bevel=0.012, taper=0.975))

    # 竿（さお）：八棱柱，上细下粗
    shaft = cylinder('shaft', 0.105, 0.083, 0.72, z=0.60, segments=8)
    apply_bevel(shaft, width=0.006, segments=1)
    parts.append(shaft)

    # 竿受（さおうけ）：托住中台的圆盘
    parts.append(cylinder('collar', 0.145, 0.135, 0.07, z=0.995, segments=16))

    # 中台（ちゅうだい）
    parts.append(box('platform', 0.50, 0.50, 0.10, z=1.08, bevel=0.012, taper=0.97))

    # 火袋（ひぶくろ）：底板 + 四角柱 + 顶板，中间留空给光
    parts.append(box('firebox_floor', 0.455, 0.455, 0.055, z=1.158, bevel=0.008))
    post = 0.072
    half = 0.188
    for i, (px, py) in enumerate((
        (-half, -half), (half, -half), (half, half), (-half, half),
    )):
        parts.append(box(f'firebox_post_{i}', post, post, 0.335, z=1.35,
                         x=px, y=py, bevel=0.006, taper=0.96))
    # 四个面的窗（用薄框条暗示，不做布尔）
    for i, (sx, sy, px, py) in enumerate((
        (0.33, 0.02, 0.0, -half), (0.33, 0.02, 0.0, half),
        (0.02, 0.33, -half, 0.0), (0.02, 0.33, half, 0.0),
    )):
        parts.append(box(f'firebox_rail_{i}', sx, sy, 0.045, z=1.235,
                         x=px, y=py, bevel=0.004))
        parts.append(box(f'firebox_rail_top_{i}', sx, sy, 0.045, z=1.468,
                         x=px, y=py, bevel=0.004))

    parts.append(box('firebox_roof_slab', 0.50, 0.50, 0.06, z=1.515, bevel=0.010, taper=0.97))

    # 笠（かさ）：四坡屋面。cone 的 4 个顶点先转 45°，让边与轴对齐
    bpy.ops.mesh.primitive_cone_add(
        vertices=4, radius1=0.625, radius2=0.045, depth=0.26,
        location=(0.0, 0.0, 1.675), rotation=(0.0, 0.0, math.radians(45.0)),
    )
    kasa = bpy.context.active_object
    kasa.name = 'kasa'
    # 檐口外沿微微抬起（起翘的极简版）：把最外圈顶点抬高 3cm
    me = kasa.data
    bottom = min(v.co.z for v in me.vertices)
    for v in me.vertices:
        if abs(v.co.z - bottom) < 1e-4:
            v.co.z += 0.03
    me.update()
    apply_bevel(kasa, width=0.010, segments=1)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    parts.append(kasa)

    # 宝珠（ほうじゅ）
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.075, segments=16, ring_count=8,
                                         location=(0.0, 0.0, 1.845))
    hoju = bpy.context.active_object
    hoju.name = 'hoju'
    for poly in hoju.data.polygons:
        poly.use_smooth = True
    parts.append(hoju)
    parts.append(cylinder('hoju_neck', 0.035, 0.05, 0.06, z=1.775, segments=12))

    # 合并成一个网格：石灯笼是一整块石构件，一个 drawcall 也符合物理
    bpy.ops.object.select_all(action='DESELECT')
    for p in parts:
        p.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.join()

    lantern = bpy.context.active_object
    lantern.name = 'SM_StoneLantern'
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    # 统一贴地
    min_z = min((lantern.matrix_world @ v.co).z for v in lantern.data.vertices)
    lantern.location.z -= min_z
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    # 平滑组：石头整体平直，只把球与圆柱侧面的硬边软化
    for poly in lantern.data.polygons:
        poly.use_smooth = False

    return lantern


# ---------------------------------------------------------------- 导出

def export_glb(obj, out_path, draco=True):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    kwargs = dict(
        filepath=out_path,
        export_format='GLB',
        use_selection=True,
        export_apply=True,
        export_yup=True,          # Blender Z-up -> glTF Y-up
        export_texcoords=True,
        export_normals=True,
        export_materials='EXPORT',
        # 强制导出活动顶点色：材质里虽然已接了节点，但不同小版本的
        # 判定逻辑不一样，显式指定更稳
        export_vertex_color='ACTIVE',
    )
    if draco:
        kwargs.update(
            export_draco_mesh_compression_enable=True,
            export_draco_mesh_compression_level=6,
        )

    # 不同 Blender 小版本的参数名有差异，逐个降级重试而不是直接崩
    for attempt in range(4):
        try:
            bpy.ops.export_scene.gltf(**kwargs)
            return
        except TypeError as exc:
            msg = str(exc)
            removed = False
            for key in list(kwargs.keys()):
                if key in msg and key not in ('filepath', 'export_format'):
                    kwargs.pop(key)
                    removed = True
            if not removed:
                raise
    raise RuntimeError('glTF 导出失败：参数与当前 Blender 版本不兼容')


# ---------------------------------------------------------------- 主流程

def main():
    argv = sys.argv
    argv = argv[argv.index('--') + 1:] if '--' in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='dist')
    parser.add_argument('--no-draco', action='store_true')
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    clean_scene()
    mat = make_stone_material()

    lantern = build_lantern()
    lantern.data.materials.clear()
    lantern.data.materials.append(mat)
    paint_weathering(lantern)

    # UV：没有贴图也要有 UV，后续烘焙 AO 时才不用重做
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.02)
    bpy.ops.object.mode_set(mode='OBJECT')

    out_path = os.path.join(out_dir, 'stone-lantern.glb')
    export_glb(lantern, out_path, draco=not args.no_draco)

    tris = sum(len(p.vertices) - 2 for p in lantern.data.polygons)
    dims = lantern.dimensions
    size = os.path.getsize(out_path)

    print('---- build summary ----')
    print(f'object      : {lantern.name}')
    print(f'vertices    : {len(lantern.data.vertices)}')
    print(f'triangles   : {tris}')
    print(f'dimensions  : {dims.x:.3f} x {dims.y:.3f} x {dims.z:.3f} m')
    print(f'glb size    : {size / 1024:.1f} KB')
    print(f'output      : {out_path}')
    print('-----------------------')

    assert dims.z > 1.5, '灯笼高度异常，检查单位'
    assert tris < 12000, f'面数超出预算：{tris}'
    print('OK')


if __name__ == '__main__':
    main()
