"""Build a tabletop MJCF from the vendored, unmodified SO-101 URDF."""
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
import mujoco

ROOT = Path(__file__).resolve().parents[1]
# World origin is the ArUco marker centre. Measure this offset on the real build.
ARM_BASE_OFFSET = (0.0, -0.12, 0.0)
JOINTS = ('shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll', 'gripper')
OBJECTS = {'obj1': ('red cube', (0.23, 0.10, 0.018), (0.92, 0.16, 0.13, 1)),
           'obj2': ('blue cube', (0.22, -0.13, 0.018), (0.12, 0.46, 0.92, 1))}


def build_model():
    source = ROOT / 'assets/so101/so101_new_calib.urdf'
    urdf = ET.parse(source).getroot()
    for mesh in urdf.findall('.//mesh'):
        mesh.set('filename', str(source.parent / mesh.get('filename')))
    # Match Pathfinder's existing lime accent, #D5FD51. Keep vendor URDF untouched.
    urdf.find("material[@name='3d_printed']/color").set('rgba', '0.835294 0.992157 0.317647 1')
    extension = ET.SubElement(urdf, 'mujoco')
    ET.SubElement(extension, 'compiler', discardvisual='false', fusestatic='false', balanceinertia='true')
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'robot.urdf'
        ET.ElementTree(urdf).write(path)
        model = mujoco.MjModel.from_xml_path(str(path))
        converted = Path(directory) / 'robot.xml'
        mujoco.mj_saveLastXML(str(converted), model)
        root = ET.parse(converted).getroot()
    root.find('compiler').set('meshdir', str(source.parent / 'assets'))
    ET.SubElement(root, 'option', timestep='0.004', gravity='0 0 -9.81', integrator='implicitfast')
    visual = ET.SubElement(root, 'visual')
    ET.SubElement(visual, 'global', offwidth='1280', offheight='720')
    ET.SubElement(visual, 'map', znear='0.005', zfar='10')
    ET.SubElement(visual, 'headlight', ambient='0.35 0.35 0.35', diffuse='0.7 0.7 0.7')
    asset = root.find('asset')
    ET.SubElement(asset, 'texture', name='grid', type='2d', builtin='checker', width='512', height='512',
                  rgb1='0.17 0.21 0.25', rgb2='0.20 0.25 0.29')
    ET.SubElement(asset, 'material', name='tabletop', texture='grid', texrepeat='12 10', reflectance='0.1')
    world = root.find('worldbody')
    root.find(".//body[@name='base_link']").set('pos', ' '.join(map(str, ARM_BASE_OFFSET)))
    ET.SubElement(asset, 'texture', name='aruco', type='2d', file=str(ROOT / 'assets/marker.png'))
    ET.SubElement(asset, 'material', name='aruco', texture='aruco', texuniform='false', reflectance='0', emission='0.4')
    ET.SubElement(world, 'geom', name='marker', type='plane', pos='0 0 0.0005', size='0.075 0.075 0.001',
                  material='aruco', contype='0', conaffinity='0')
    ET.SubElement(world, 'light', pos='0.3 -0.5 1.5', dir='0 0 -1', diffuse='0.8 0.8 0.8')
    ET.SubElement(world, 'geom', name='table', type='box', pos='0.2 0 -0.025', size='0.6 0.5 0.025', material='tabletop', conaffinity='3')
    ET.SubElement(world, 'geom', name='drop_zone', type='box', pos='0.18 -0.20 0.001', size='0.055 0.055 0.001',
                  rgba='0.25 0.75 0.58 0.5', contype='0', conaffinity='0')
    for name, (_, position, colour) in OBJECTS.items():
        body = ET.SubElement(world, 'body', name=name, pos=' '.join(map(str, position)))
        ET.SubElement(body, 'freejoint', name=name+'_free')
        ET.SubElement(body, 'geom', name=name+'_geom', type='box', size='0.018 0.018 0.018', mass='0.025',
                      rgba=' '.join(map(str, colour)), friction='0.9 0.01 0.001')
    hand = ET.SubElement(world, 'body', name='hand', mocap='true', pos='0.6 0.4 0.3')
    ET.SubElement(hand, 'geom', name='hand_geom', type='ellipsoid', size='0.035 0.045 0.015', rgba='1 0.65 0.43 0', contype='0', conaffinity='0')
    ET.SubElement(hand, 'geom', name='hand_forearm', type='capsule', fromto='0 -0.13 0 0 -0.035 0', size='0.023', rgba='1 0.65 0.43 0', contype='0', conaffinity='0')
    for finger in range(5):
        x = (finger - 2) * .014
        ET.SubElement(hand, 'geom', name=f'hand_finger_{finger}', type='capsule', fromto=f'{x} .025 0 {x} {0.07 - abs(finger-2)*.006} .003', size='.005', rgba='1 .65 .43 0', contype='0', conaffinity='0')
    gripper = root.find(".//body[@name='gripper_link']")
    ET.SubElement(gripper, 'site', name='tcp', pos='-0.0079 -0.000218121 -0.0981274', size='0.004', rgba='0.3 1 0.8 1')
    # Robot servo dynamics are deliberately kinematic. Objects still use contact/gravity.
    for geom in root.findall('.//geom'):
        if geom.get('type') == 'mesh' and geom.get('contype') != '0':
            geom.set('friction', '0.8 0.01 0.001')
            geom.set('group', '3')
            geom.set('contype', '2')
            geom.set('conaffinity', '3')
    # Ideal-grasp fingers do not push objects away while closing.
    for name in ('gripper_link', 'moving_jaw_so101_v1_link'):
        for geom in root.find(f".//body[@name='{name}']").findall('geom'):
            geom.set('contype', '2')
            geom.set('conaffinity', '2')
    xml = ET.tostring(root, encoding='unicode')
    return mujoco.MjModel.from_xml_string(xml)
