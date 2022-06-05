'''
Created on Mar 2, 2017

@author: Patrick, Georgi
'''
'''
https://pymotw.com/2/xml/etree/ElementTree/create.html
https://docs.python.org/2/library/xml.etree.elementtree.html
https://www.na-mic.org/Wiki/index.php/AHM2012-Slicer-Python
https://www.slicer.org/wiki/Documentation/Nightly/ScriptRepository
https://gist.github.com/ungi/4b0bd3a109bd98de054c66cc1ec6cfab
http://stackoverflow.com/questions/6597552/mathematica-write-matrix-data-to-xml-read-matrix-data-from-xml

#handling updated status in Slicer and in Blender
http://stackoverflow.com/questions/1977362/how-to-create-module-wide-variables-in-python

#Panel List
http://blender.stackexchange.com/questions/14202/index-out-of-range-for-uilist-causes-panel-crash/14203#14203

'''
bl_info = {
    "name": "openPlan",
    "author": "Georgi Talmazov, Patrick R. Moore",
    "version": (3, 1),
    "blender": (2, 93, 0),
    "location": "3D View -> UI SIDE PANEL",
    "description": "Blender and 3D Slicer sync add-on.",
    "warning": "",
    "wiki_url": "",
    "category": "Dental",
    }
#python
import os
import inspect
import time
import numpy as np
from numpy import nan
from mathutils import Matrix
import queue
import subprocess

import math

#Blender
import bpy
import bmesh

#XML
from xml.etree import ElementTree as ET
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, Comment, ElementTree, tostring, fromstring

#Blender
from bpy.types import Operator, AddonPreferences
from bpy.app.handlers import persistent
from io_mesh_ply import export_ply

#TCP sock lib
from .slicer_module import comm as asyncsock

def matrix_to_xml_element(mx):
    nrow = len(mx.row)
    ncol = len(mx.row[0])
    
    xml_mx = Element('matrix')
    
    for i in range(0,nrow):
        xml_row = SubElement(xml_mx, 'row')
        for j in range(0,ncol):
            mx_entry = SubElement(xml_row, 'entry')
            mx_entry.text = str(mx[i][j])
            
    return xml_mx

def material_to_xml_element(mat):
    
    xml_mat = Element('material')

    r = SubElement(xml_mat, 'r')
    r.text = str(round(mat.diffuse_color[0],4))
    g = SubElement(xml_mat, 'g')
    g.text = str(round(mat.diffuse_color[1],4))
    b = SubElement(xml_mat, 'b')
    b.text = str(round(mat.diffuse_color[2],4))
    a = SubElement(xml_mat, 'a')
    a.text = str(round(mat.diffuse_color[3],2))
    
    return xml_mat

#a box to hold stuff in
class Box:
    pass

__m = Box()
__m.last_update = time.time()
__m.ob_names = []
__m.transform_cache = {}


def detect_transforms(group='SlicerLink'):
    if group not in bpy.data.collections:
        return None
    
    changed = []
    sg = bpy.data.collections[group]
    for ob in sg.objects:
        if ob.name not in __m.transform_cache:
            changed += [ob.name]
            #
            #__m.transform_cache[ob.name] = ob.matrix_world.copy()
            
        elif not np.allclose(ob.matrix_world, __m.transform_cache[ob.name]):
            changed += [ob.name]
            #don't update until we know slicer has implemented previous changes
            #__m.transform_cache[ob.name] = ob.matrix_world.copy()
            
    if len(changed) == 0: return None
    return changed    

def select_b_obj(modelName):
    bpy.ops.object.select_all(action='DESELECT')
    bpy.data.objects.get(modelName).select_set(True)
    bpy.context.view_layer.objects.active = bpy.data.objects.get(modelName)
    
    bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')
    

def import_obj_from_slicer(data):
    #ShowMessageBox("Received object from Slicer.", "openPlan Info:")
    obj, xml = data.split("_XMLDATA_")
    obj_points, obj_polys = obj.split("_POLYS_")
    obj_points = eval(obj_points)
    obj_polys = eval(obj_polys)
    blender_faces = []
    offset = 0 #unflatten the list from slicer
    while ( offset < len(obj_polys)):
        vertices_per_face = obj_polys[offset]
        offset += 1
        vertex_indices = obj_polys[offset : offset + vertices_per_face]
        blender_faces.append(vertex_indices)
        offset += vertices_per_face
    handlers = [hand.__name__ for hand in bpy.app.handlers.depsgraph_update_post]
    if "export_to_slicer" not in handlers:
        bpy.app.handlers.depsgraph_update_post.append(export_to_slicer) 
    if "SlicerLink" not in bpy.data.collections:
        sg = bpy.data.collections.new('SlicerLink')
    else:
        sg = bpy.data.collections['SlicerLink']
    #sg = bpy.data.collections['SlicerLink']
    tree = ElementTree(fromstring(xml))
    x_scene = tree.getroot()
    #we are expecting one object per packet from slicer, so no need to iterate the XML object tree
    new_mesh = bpy.data.meshes.new(x_scene[0].get('name')+"_data")
    new_mesh.from_pydata(obj_points, [], blender_faces)
    new_mesh.update()
    new_object = bpy.data.objects.new(x_scene[0].get('name'), new_mesh)
    new_object.data = new_mesh
    scene = bpy.context.scene
    bpy.context.scene.collection.objects.link(new_object)

    sg.objects.link(new_object)
    write_ob_transforms_to_cache(sg.objects)

    #if bpy.data.objects[x_scene[0].get('name')].active_material is None:
    material = bpy.data.materials.new(name=x_scene[0].get('name')+"_mat")
    new_object.data.materials.append(material)
    xml_mat = x_scene[0].find('material')
    new_object.active_material.diffuse_color = (float(xml_mat[0].text), float(xml_mat[1].text), float(xml_mat[2].text), float(xml_mat[3].text))

    #new_object.data.transform(matrix)
    #new_object.data.update()

def FILE_import_obj_from_slicer(data, group = 'SlicerLink'):
    addons = bpy.context.preferences.addons
    settings = addons[__name__].preferences
    handlers = [hand.__name__ for hand in bpy.app.handlers.depsgraph_update_post]
    if "export_to_slicer" not in handlers:
        bpy.app.handlers.depsgraph_update_post.append(export_to_slicer) 
                    
    if group not in bpy.data.collections:
        sg = bpy.data.collections.new(group)
    else:
        sg = bpy.data.collections[group]

    xml = data
    tree = ElementTree(fromstring(xml))
    x_scene = tree.getroot()
    #we are expecting one object per packet from slicer, so no need to iterate the XML object tree
    bpy.ops.import_mesh.ply(filepath=os.path.join(settings.tmp_dir, x_scene[0].get('name') + ".ply"))
    bpy.context.scene.collection.objects.link(bpy.data.objects[x_scene[0].get('name')])

    sg.objects.link(bpy.data.objects[x_scene[0].get('name')])
    write_ob_transforms_to_cache(sg.objects)

    os.remove(os.path.join(settings.tmp_dir, x_scene[0].get('name') + ".ply"))

def send_obj_to_slicer(objects = [], group = 'SlicerLink'):
    if asyncsock.socket_obj is not None:
        handlers = [hand.__name__ for hand in bpy.app.handlers.depsgraph_update_post]
        if "export_to_slicer" not in handlers:
            bpy.app.handlers.depsgraph_update_post.append(export_to_slicer) 
            
        if group not in bpy.data.collections:
            sg = bpy.data.collections.new(group)
        else:
            sg = bpy.data.collections[group]

        if len(objects) == 1:
            ob = bpy.data.objects[objects[0]]
            #slicer does not like . in ob names
            if "." in ob.name:
                ob.name.replace(".","_")

            me = ob.to_mesh(preserve_all_data_layers=False, depsgraph=None)
            #if me:
            #    return
            if bpy.context.scene.legacy_sync == True and len(me.vertices) > bpy.context.scene.legacy_vertex_threshold:
                addons = bpy.context.preferences.addons
                settings = addons[__name__].preferences
                if not os.path.exists(settings.tmp_dir):
                    print("Temp dir does not exist")
                else:
                    temp_file = os.path.join(settings.tmp_dir, ob.name + ".ply")
                    ret = export_ply.save_mesh(temp_file, me,
                            use_normals=False,
                            use_uv_coords=False,
                            use_colors=False,
                            use_ascii=False,
                            )

                    x_scene = build_xml_scene([ob], group)
                    xml_str = tostring(x_scene).decode()
                    asyncsock.socket_obj.sock_handler[0].send_data("FILE_OBJ", xml_str)
            else:
                obj_verts = [list(v.co) for v in me.vertices]
                tot_verts = len(obj_verts[0])
                obj_poly = []
                for poly in me.polygons:
                    obj_poly.append(tot_verts)
                    for v in poly.vertices:
                        obj_poly.append(v)
                x_scene = build_xml_scene([ob], group)
            
                xml_str = tostring(x_scene).decode() #, encoding='unicode', method='xml')
                packet = "%s_POLYS_%s_XMLDATA_%s"%(obj_verts, obj_poly, xml_str)

                #ShowMessageBox("Sending object to Slicer.", "openPlan Info:")

                asyncsock.socket_obj.sock_handler[0].send_data("OBJ", packet)
            ob.to_mesh_clear()

            if ob.name in sg.objects:
                return
            else:
                sg.objects.link(ob)

        elif len(objects) > 1:
            total_vertices = 0
            for ob in objects: #[TODO] object group managment 
                me = bpy.data.objects[ob].to_mesh(preserve_all_data_layers=False, depsgraph=None)
                total_vertices += len(me.vertices)
            packet = ""
            #print(total_vertices)
            for ob in objects: #[TODO] object group managment 
                ob = bpy.data.objects[ob]
                #slicer does not like . in ob names
                if "." in ob.name:
                    ob.name.replace(".","_")

                me = ob.to_mesh(preserve_all_data_layers=False, depsgraph=None)
                if not me:
                    continue

                
                if bpy.context.scene.legacy_sync == True and total_vertices > bpy.context.scene.legacy_vertex_threshold:
                    addons = bpy.context.preferences.addons
                    settings = addons[__name__].preferences
                    if not os.path.exists(settings.tmp_dir):
                        print("Temp dir does not exist")
                    else:
                        temp_file = os.path.join(settings.tmp_dir, ob.name + ".ply")
                        ret = export_ply.save_mesh(temp_file, me,
                                use_normals=False,
                                use_uv_coords=False,
                                use_colors=False,
                                use_ascii=False,
                                )

                        x_scene = build_xml_scene([ob], group)
                        xml_str = tostring(x_scene).decode()
                        packet = packet + "%s_XMLDATA_"%(xml_str)
                else:
                    obj_verts = [list(v.co) for v in me.vertices]
                    tot_verts = len(obj_verts[0])
                    obj_poly = []
                    for poly in me.polygons:
                        obj_poly.append(tot_verts)
                        for v in poly.vertices:
                            obj_poly.append(v)
                    x_scene = build_xml_scene([ob], group)
                
                    xml_str = tostring(x_scene).decode() #, encoding='unicode', method='xml')
                    packet = packet + "%s_POLYS_%s_XMLDATA_%s_N_OBJ_"%(obj_verts, obj_poly, xml_str)

                    #ShowMessageBox("Sending object to Slicer.", "openPlan Info:")

                    #asyncsock.socket_obj.sock_handler[0].send_data("OBJ", packet)
                ob.to_mesh_clear()

                if ob.name in sg.objects:
                    continue
                else:
                    sg.objects.link(ob)

            if total_vertices < bpy.context.scene.legacy_vertex_threshold:
                asyncsock.socket_obj.sock_handler[0].send_data("OBJ_MULTIPLE", packet[:-len("_N_OBJ_")])
            elif bpy.context.scene.legacy_sync == True and total_vertices > bpy.context.scene.legacy_vertex_threshold:
                asyncsock.socket_obj.sock_handler[0].send_data("FILE_OBJ_MULTIPLE", packet[:-len("_XMLDATA_")])

        write_ob_transforms_to_cache(sg.objects)

def obj_check_handle(data):
    status, obj_name = data.split("_BREAK_")
    
    #ShowMessageBox(status, "openPlan Info:")

    handlers = [hand.__name__ for hand in bpy.app.handlers.depsgraph_update_post]
    if "export_to_slicer" not in handlers:
        bpy.app.handlers.depsgraph_update_post.append(export_to_slicer) 
                    
    if "SlicerLink" not in bpy.data.collections:
        sg = bpy.data.collections.new('SlicerLink')
    else:
        sg = bpy.data.collections['SlicerLink']
    if status == "STATUS":
        #print([ob.name for ob in bpy.data.collections['SlicerLink'].objects[:]])
        #print(obj_name)
        #print([ob.name for ob in bpy.data.objects[:]])
        link_col_found = obj_name in [ob.name for ob in bpy.data.collections['SlicerLink'].objects[:]]
        b_obj_exist = obj_name in [ob.name for ob in bpy.data.objects[:]]
        if link_col_found == True and b_obj_exist == True:
            asyncsock.socket_obj.sock_handler[0].send_data("CHECK", "LINKED_BREAK_" + obj_name)
        elif link_col_found == False and b_obj_exist == True:
            asyncsock.socket_obj.sock_handler[0].send_data("CHECK", "NOT LINKED_BREAK_" + obj_name)
        elif link_col_found == False and b_obj_exist == False:
            asyncsock.socket_obj.sock_handler[0].send_data("CHECK", "MISSING_BREAK_" + obj_name)
    elif status == "LINK":
        sg.objects.link(bpy.data.objects[obj_name])
        write_ob_transforms_to_cache(sg.objects)
    elif status == "LINK_MULTIPLE":
        obj_name = obj_name.split(",")
        for obj in obj_name:
            sg.objects.link(bpy.data.objects[obj])
        write_ob_transforms_to_cache(sg.objects)
    elif status == "MISSING":
        send_obj_to_slicer([obj_name], "SlicerLink")
    elif status == "MISSING_MULTIPLE":
        obj_name = obj_name.split(",")
        #print(obj_name)
        send_obj_to_slicer(obj_name, "SlicerLink")
    elif status == "LINK+MISSING_MULTIPLE":
        unlinked, missing = obj_name.split(";")
        unlinked = unlinked.split(",")
        missing = missing.split(",")
        send_obj_to_slicer(missing, "SlicerLink")
        for obj in unlinked:
            sg.objects.link(bpy.data.objects[obj])
        write_ob_transforms_to_cache(sg.objects)
    elif status == "UNLINK":
        sg.objects.unlink(bpy.data.objects[obj_name])
        write_ob_transforms_to_cache(sg.objects)

def obj_check_send():
    #ShowMessageBox("Checking object.", "openPlan Info:")

    handlers = [hand.__name__ for hand in bpy.app.handlers.depsgraph_update_post]
    if "export_to_slicer" not in handlers:
        bpy.app.handlers.depsgraph_update_post.append(export_to_slicer) 
                    
    if "SlicerLink" not in bpy.data.collections:
        sg = bpy.data.collections.new('SlicerLink')
    else:
        sg = bpy.data.collections['SlicerLink']
    #print(bpy.context.selected_objects)
    if not len(bpy.context.selected_objects) == 0 and len(bpy.context.selected_objects) == 1:
        if bpy.context.selected_objects[0].name not in bpy.data.collections['SlicerLink'].objects:
            asyncsock.socket_obj.sock_handler[0].send_data("CHECK", "STATUS_BREAK_" + bpy.context.selected_objects[0].name)
    elif not len(bpy.context.selected_objects) == 0 and len(bpy.context.selected_objects) > 1:
        names = ""
        for ob in bpy.context.selected_objects:
            if ob.name not in bpy.data.collections['SlicerLink'].objects:
                names = names + ob.name + ","
        asyncsock.socket_obj.sock_handler[0].send_data("CHECK", "STATUS_MULTIPLE_BREAK_" + names[:-1])

def update_scene_blender(xml):
    #time.sleep(0.5)
    tmp_rmv_sg = False
    bpy.ops.object.select_all(action='DESELECT')
    #print(xml)
    tree = ElementTree(fromstring(xml))
    x_scene = tree.getroot()
    bpy.data.objects[x_scene[0].get('name')].select_set(True)
    group = x_scene[0].get('group')
    if x_scene[0].get('name') in bpy.data.collections[group].objects and group == "ViewLink":
        bpy.data.collections[group].objects.unlink(bpy.data.objects[x_scene[0].get('name')])
        tmp_rmv_sg = True
    xml_mx = x_scene[0].find('matrix')
    my_matrix = []
    for i in range(0,4):
        col = []
        for j in range(0,4):
            col.append(float(xml_mx[i][j].text))
        my_matrix.append(col)
    '''
    for i in range(0,3):
        my_matrix[i][3] = my_matrix[3][i]
        my_matrix[3][i] = 0.0
    '''

    my_matrix = Matrix(my_matrix)
    #print(my_matrix)
    bpy.data.objects[x_scene[0].get('name')].matrix_world = my_matrix
    if bpy.data.objects[x_scene[0].get('name')].active_material is not None:
        xml_mat = x_scene[0].find('material')
        bpy.data.objects[x_scene[0].get('name')].active_material.diffuse_color = (float(xml_mat[0].text), float(xml_mat[1].text), float(xml_mat[2].text), float(xml_mat[3].text))


    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    if tmp_rmv_sg == True: bpy.data.collections[group].objects.link(bpy.data.objects[x_scene[0].get('name')])


def resize_slice_plane(planeBMesh, width, height, axes):
    dims = [width, height]
    bm = planeBMesh
    # from https://blenderartists.org/t/edge-resizer-operator/634873/7
    # https://blenderartists.org/uploads/default/original/4X/e/1/1/e1116b9c030b3b0f7f1963e9d52a4995a5e77887.py
    #me = bpy.context.object.data
    #bm = bmesh.new()
    #bm.from_mesh(me)
    #length that we want for the edge

    for dim in dims:
        wanted_length = dim

        #we want to modify only the active edge and the selected edge "follow"
        bm.select_history.clear()
        
        if hasattr(bm.verts, "ensure_lookup_table"): 
            bm.edges.ensure_lookup_table()
            # only if you need to:
            # bm.edges.ensure_lookup_table()   
            # bm.faces.ensure_lookup_table()
        
        edge = bm.edges[axes[0]]
        edge.select_set(True)
        bm.select_history.add(edge)
        edge = bm.edges[axes[1]]
        edge.select_set(True)
        bm.select_history.add(edge)

        e = bm.select_history.active
        v1 = e.verts[0]
        v2 = e.verts[1]
        
        
        #which vertex should be v1, it must be the vertex linked to another selected edge, we count the link of each vertex, the one with the much linked vertices (should be 1 vs 2 if we are in front of a nice user ;) 
        
        switch = 0
        
        for el in bm.edges:
            if el.select:
                vl1 = el.verts[0]
                vl2 = el.verts[1]
                
                if vl1 == v1 or vl2 == v1:
                    switch+=1
                    
                if vl1 == v2 or vl2 == v2:
                    switch-=1
                    
        #print(switch)
        
        if switch<0:
            v1,v2 = v2,v1
        
        l = math.sqrt(
        (v1.co.x - v2.co.x)*(v1.co.x - v2.co.x)+
        (v1.co.y - v2.co.y)*(v1.co.y - v2.co.y)+
        (v1.co.z - v2.co.z)*(v1.co.z - v2.co.z))
        #print(l)
        ratio1 = wanted_length/l
        ratio2 = wanted_length/l
        #we've calculate the ratio needed to multiply the edge at the good size
        #we remove one, now we know the length to add to have the good size
        ratio1-=1
        ratio2-=1   		 
        
        #print(ratio1)
        #print(ratio2)
        #print("-----")
        x1 = (v1.co.x - v2.co.x)*ratio1
        y1 = (v1.co.y - v2.co.y)*ratio1
        z1 = (v1.co.z - v2.co.z)*ratio1
        
        
        v1.co.x += x1
        v1.co.y += y1
        v1.co.z += z1
        
        #the vertices that we shoudn't touch
        done = [v1,v2]
        
        for el in bm.edges:
            if el.select:
                vl1 = el.verts[0]
                vl2 = el.verts[1]
                
                if vl1 not in done:
                    done.append(vl1)
                    vl1.co.x += x1
                    vl1.co.y += y1
                    vl1.co.z += z1
                
                if vl2 not in done:
                    done.append(vl2)
                    vl2.co.x += x1
                    vl2.co.y += y1
                    vl2.co.z += z1
                    
        axes.reverse()

    return bm

#https://github.com/florianfelix/io_import_images_as_planes_rewrite/blob/master/io_import_images_as_planes.py#L918
def live_img_update(image):
    sliceName, modelName, image_dim, plane_dim, image_np = image.split("_BREAK_")
    image_dim = eval(image_dim)
    plane_dim = eval(plane_dim)
    image_w, image_h = image_dim[0], image_dim[1]
    plane_w, plane_h = plane_dim[0], plane_dim[1]
    image_np = eval(image_np)
    #print(image_np)
    if sliceName not in bpy.data.images.keys():
        bpy.data.images.new(sliceName, width=20, height=20, alpha=True, float_buffer=True)
        engine = bpy.context.scene.render.engine
        if engine in {'CYCLES', 'BLENDER_EEVEE', 'BLENDER_OPENGL'}:
            material = create_cycles_material(bpy.context, sliceName, bpy.data.images[sliceName])
            bpy.data.objects[modelName].data.materials.append(material)
    outputImg = bpy.data.images[sliceName]
    if not outputImg.generated_width == image_w or not outputImg.generated_height == image_h:
        outputImg.generated_width = image_w
        outputImg.generated_height = image_h

    outputImg.pixels = ((np.asarray(image_np))*1/255).flatten()

    me = bpy.data.meshes.get(modelName)
    bm = bmesh.new()
    bm.from_mesh(me)
    if hasattr(bm.verts, "ensure_lookup_table"): 
            bm.edges.ensure_lookup_table()
            # only if you need to:
            # bm.edges.ensure_lookup_table()   
            # bm.faces.ensure_lookup_table()
    '''
    print(int(bm.edges[0].calc_length()))
    print(int(plane_h/10))
    print(int(bm.edges[1].calc_length()))
    print(int(plane_w/10))
    '''
    if not int(bm.edges[0].calc_length()) == int(plane_h/10) or not int(bm.edges[1].calc_length()) == int(plane_w/10):
        bm = resize_slice_plane(bm, plane_w/10, plane_h/10, [0,1])
        bm.to_mesh(me)
        bm.free()
        me.update()
        bm = None
        #asyncsock.socket_obj.sock_handler[0].send_data("DEL", modelName)
        bpy.context.view_layer.objects.active = bpy.data.objects.get(modelName)
        bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')
        send_obj_to_slicer([modelName], "ViewLink")
        print("plane replaced!")
    if bm is not None:
        bm.free()
        me.update()
        print("plane NOT replaced")
    #bpy.context.view_layer.objects.active = bpy.data.objects.get("Plane")
    #bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')
    #set our delete mode
    

def clean_node_tree(node_tree):
    """Clear all nodes in a shader node tree except the output.
    Returns the output node
    """
    nodes = node_tree.nodes
    for node in list(nodes):  # copy to avoid altering the loop's data source
        if not node.type == 'OUTPUT_MATERIAL':
            nodes.remove(node)

    return node_tree.nodes[0]

def create_cycles_material(context, sliceName, img_spec):
    image = img_spec
    image.alpha_mode = "STRAIGHT" #or NONE
    name_compat = sliceName
    material = None
    for mat in bpy.data.materials:
        if mat.name == name_compat:
            material = mat
    if not material:
        material = bpy.data.materials.new(name=name_compat)

    material.use_nodes = True
    node_tree = material.node_tree
    out_node = clean_node_tree(node_tree)

    #tex_image = create_cycles_texnode(context, node_tree, img_spec)
    tex_image = node_tree.nodes.new('ShaderNodeTexImage')
    tex_image.image = bpy.data.images[sliceName]
    node_tree.links.new(out_node.inputs[0], tex_image.outputs[0])


    return material


@persistent
def export_to_slicer(scene):
    #check for changes
    changed_obj = detect_transforms()
    changed_view = detect_transforms("ViewLink")

    if changed_obj is not None:
        #update the transform cache
        for ob_name in changed_obj:
            if ob_name not in bpy.data.objects: continue
            __m.transform_cache[ob_name] = bpy.data.objects[ob_name].matrix_world.copy()
        obs = [bpy.data.objects.get(ob_name) for ob_name in changed_obj if bpy.data.objects.get(ob_name) and ob_name in bpy.data.collections['SlicerLink'].objects]
        if obs is not []:
            x_scene = build_xml_scene(obs, 'SlicerLink')
            xml_str = tostring(x_scene).decode()
            asyncsock.socket_obj.sock_handler[0].send_data("XML", xml_str)
        
    if changed_view is not None:
        #update the transform cache
        for ob_name in changed_view:
            if ob_name not in bpy.data.objects: continue
            __m.transform_cache[ob_name] = bpy.data.objects[ob_name].matrix_world.copy()
        view_obs = [bpy.data.objects.get(ob_name) for ob_name in changed_view if bpy.data.objects.get(ob_name) and ob_name in bpy.data.collections["ViewLink"].objects]
        if view_obs is not []:
            x_scene = build_xml_scene(view_obs, "ViewLink")
            xml_str = tostring(x_scene).decode()
            asyncsock.socket_obj.sock_handler[0].send_data("VIEW_UPDATE", xml_str)
            print("sent view update command")
    

    """
    #limit refresh rate to keep blender smooth    
    now = time.time()
    if now - __m.last_update < .2: return #TODO time limit
    __m.last_update = time.time()
    """
            
def write_ob_transforms_to_cache(obs):
    __m.ob_names = []
    for ob in obs:
        __m.transform_cache[ob.name] = ob.matrix_world.copy()
        __m.ob_names += [ob.name]

def build_xml_scene(obs, group):
    '''
    obs - list of blender objects
    file - filepath to write the xml
    '''
        
    x_scene = Element('scene')
    
    for ob in obs:
        xob = SubElement(x_scene, 'b_object')
        xob.set('name', ob.name)
        xob.set('group', group)
        
        xmlmx = matrix_to_xml_element(ob.matrix_world)
        xob.extend([xmlmx])
        
        if len(ob.material_slots):
            mat = ob.material_slots[0].material
            xmlmat = material_to_xml_element(mat)
            xob.extend([xmlmat])
    
    return x_scene

class SelectedtoSlicerGroup(bpy.types.Operator):
    """
    Add selected objects to the SlicerLink group or
    replace the SlicerLing group with selected objects
    """
    bl_idname = "object.slicergroup"
    bl_label = "Slicer Group"
    
    def execute(self,context):
        
          
        if "SlicerLink" not in bpy.data.collections:
            sg = bpy.data.collections.new('SlicerLink')
        else:
            sg = bpy.data.collections['SlicerLink']
          
        if bpy.types.Scene.overwrite:
            for ob in sg.objects:
                sg.objects.unlink(ob)
                
        for ob in context.selected_objects: #[TODO] object group managments
            #slicer does not like . in ob names
            if ob.name in sg.objects:
                continue
            else:
                sg.objects.link(ob)
        
        #I had to split the fn off because I could not reference
        #__m within the operator class, it seemed to think it
        #had to belong to the SlicerToGroup class.
        write_ob_transforms_to_cache(sg.objects)
        
        return {'FINISHED'}

class StartSlicerLinkServer(bpy.types.Operator):
    """
    Start updating slicer live by adding a scene_update_post/depsgraph_update_post (2.8) handler
    """
    bl_idname = "link_slicer.slicer_link_server_start"
    bl_label = "Server"
    
    def execute(self,context):
        if asyncsock.socket_obj == None:
            asyncsock.socket_obj = asyncsock.BlenderComm.EchoServer(context.scene.host_addr, int(context.scene.host_port), [("XML", update_scene_blender),("OBJ", import_obj_from_slicer), ("CHECK", obj_check_handle), ("SLICE_UPDATE", live_img_update), ("FILE_OBJ", FILE_import_obj_from_slicer), ("SELECT_OBJ", select_b_obj)], {"legacy_sync" : context.scene.legacy_sync, "legacy_vertex_threshold" : context.scene.legacy_vertex_threshold}, context.scene.debug_log)
            asyncsock.thread = asyncsock.BlenderComm.init_thread(asyncsock.BlenderComm.start, asyncsock.socket_obj)
            context.scene.socket_state = "SERVER"

            #over-riding the DEL key. not elegant but ok for now
            wm = bpy.context.window_manager
            km = wm.keyconfigs.addon.keymaps.new(name='Object Mode', space_type='EMPTY')
            kmi = km.keymap_items.new('link_slicer.delete_objects_both', 'DEL', 'PRESS')
            bpy.ops.wm.modal_timer_operator("INVOKE_DEFAULT")
            ShowMessageBox("Server started.", "openPlan Info:")

            for group in ['SlicerLink', "ViewLink"]:
                if group not in bpy.data.collections:
                    sg = bpy.data.collections.new(group)
        return {'FINISHED'}

import platform
class Start3DSlicer(bpy.types.Operator):
    """
    Start updating slicer live by adding a scene_update_post/depsgraph_update_post (2.8) handler
    """
    bl_idname = "link_slicer.slicer_init"
    bl_label = "Open Image"
    
    def execute(self,context):
        if asyncsock.socket_obj == None and context.scene.DICOM_dir is not None:
            if os.path.splitext(context.scene.DICOM_dir)[1].lower() == ".dcm":
                slicer_startup_parameters = ''.join((
                    "dicomDataDir = '%s'\n"%os.path.dirname(context.scene.DICOM_dir).replace(os.sep, '/'),
                    "loadedNodeIDs = []\n",
                    "from DICOMLib import DICOMUtils\n",
                    "with DICOMUtils.TemporaryDICOMDatabase() as db:\n",
                    "\tDICOMUtils.importDicom(dicomDataDir, db)\n",
                    "\tpatientUIDs = db.patients()\n",
                    "\tfor patientUID in patientUIDs:\n",
                    "\t\tloadedNodeIDs.extend(DICOMUtils.loadPatientByUID(patientUID))\n",
                    "slicer.util.selectModule('BlenderMonitor')\n",
                    "slicer.util.setSliceViewerLayers(background=slicer.util.getNode(loadedNodeIDs[0]))\n",
                    "slicer.util.getModule('BlenderMonitor').widgetRepresentation().self().workingVolume = slicer.util.getNode(loadedNodeIDs[0])"
                ))
            elif os.path.splitext(context.scene.DICOM_dir)[1].lower() == ".mrb":
                slicer_startup_parameters = ''.join((
                    "slicer.util.selectModule('BlenderMonitor')\n",
                    "slicer.util.loadScene('%s')\n"%(context.scene.DICOM_dir.replace(os.sep, '/')), #this has to be a supported 3d slicer file, there is no check to ensure that
                ))
                if len(context.scene.linked_models) > 0:
                    models = ""
                    for model in context.scene.linked_models:
                        models += model.name + ","
                    slicer_startup_parameters += slicer_startup_parameters.join((
                        "slicer.util.getModule('BlenderMonitor').widgetRepresentation().self().sock.send_data('CHECK', 'LINK_MULTIPLE_BREAK_%s')\n"%(models[:-1]),
                    ))
            else:
                slicer_startup_parameters = ''.join((
                    "volumeNode = slicer.util.loadVolume('%s')\n"%(context.scene.DICOM_dir.replace(os.sep, '/')), #this has to be a supported 3d slicer file, there is no check to ensure that
                    "slicer.util.selectModule('BlenderMonitor')\n",
                    "slicer.util.setSliceViewerLayers(background=volumeNode)\n",
                    "slicer.util.getModule('BlenderMonitor').widgetRepresentation().self().workingVolume = volumeNode"
                ))
            if platform.system() == "Windows": #windows support
                asyncsock.slicer_sysprocess = subprocess.Popen([os.path.join(bpy.context.preferences.addons[__name__].preferences.dir_3d_slicer), "--python-code", slicer_startup_parameters])
            elif platform.system() == "Darwin": #macOS support
                bpy.context.preferences.addons[__name__].preferences.dir_3d_slicer = "/Applications/Slicer.app/Contents/MacOS/Slicer"
                asyncsock.slicer_sysprocess = subprocess.Popen([bpy.context.preferences.addons[__name__].preferences.dir_3d_slicer, "--python-code", slicer_startup_parameters])
            asyncsock.socket_obj = asyncsock.BlenderComm.EchoServer(context.scene.host_addr, int(context.scene.host_port), [("XML", update_scene_blender),("OBJ", import_obj_from_slicer), ("CHECK", obj_check_handle), ("SLICE_UPDATE", live_img_update), ("FILE_OBJ", FILE_import_obj_from_slicer), ("SELECT_OBJ", select_b_obj)], {"legacy_sync" : context.scene.legacy_sync, "legacy_vertex_threshold" : context.scene.legacy_vertex_threshold}, context.scene.debug_log)
            asyncsock.thread = asyncsock.BlenderComm.init_thread(asyncsock.BlenderComm.start, asyncsock.socket_obj)
            context.scene.socket_state = "SERVER"

            #over-riding the DEL key. not elegant but ok for now
            wm = bpy.context.window_manager
            km = wm.keyconfigs.addon.keymaps.new(name='Object Mode', space_type='EMPTY')
            kmi = km.keymap_items.new('link_slicer.delete_objects_both', 'DEL', 'PRESS')
            bpy.ops.wm.modal_timer_operator("INVOKE_DEFAULT")
            ShowMessageBox("3D Slicer started.", "openPlan Info:")

            for group in ['SlicerLink', "ViewLink"]:
                if group not in bpy.data.collections:
                    sg = bpy.data.collections.new(group)
                
        return {'FINISHED'}

class StartSlicerLinkClient(bpy.types.Operator):
    """
    Start updating slicer live by adding a scene_update_post/depsgraph_update_post (2.8) handler
    """
    bl_idname = "link_slicer.slicer_link_client_start"
    bl_label = "Client"
    
    def execute(self,context):
        ShowMessageBox("Client not yet available.", "openPlan Info:")
        return {'FINISHED'}
        if asyncsock.socket_obj == None:
            asyncsock.socket_obj = asyncsock.BlenderComm.EchoClient(context.scene.host_addr, int(context.scene.host_port))
            asyncsock.thread = asyncsock.BlenderComm.init_thread(asyncsock.BlenderComm.start)
            context.scene.socket_state = "CLIENT"
            print("client started -> ")
        return {'FINISHED'}

class linkObjectsToSlicer(bpy.types.Operator):
    """
    Start updating slicer live by adding a scene_update_post/depsgraph_update_post (2.8) handler
    """
    bl_idname = "link_slicer.link_objects_to_slicer"
    bl_label = "Link Object(s)"
    
    def execute(self,context):
        if not asyncsock.socket_obj == None:
            for ob in context.selected_objects:
                TRIANGULATE_mod = ob.modifiers.new(name='triangles4slicer_' + ob.name, type="TRIANGULATE")
                context.view_layer.objects.active = ob
                bpy.ops.object.modifier_apply(modifier=TRIANGULATE_mod.name)

            obj_check_send()
        return {'FINISHED'}

class unlinkObjectsFromSlicer(bpy.types.Operator):
    """
    Start updating slicer live by adding a scene_update_post/depsgraph_update_post (2.8) handler
    """
    bl_idname = "link_slicer.unlink_objects_from_slicer"
    bl_label = "Unlink Object(s)"
    
    def execute(self,context):
        if not asyncsock.socket_obj == None:
            if "SlicerLink" not in bpy.data.collections:
                sg = bpy.data.collections.new('SlicerLink')
            else:
                sg = bpy.data.collections['SlicerLink']

            for ob in bpy.context.selected_objects:
                sg.objects.unlink(ob)
                asyncsock.socket_obj.sock_handler[0].send_data("CHECK", "UNLINK_BREAK_" + ob.name)
                write_ob_transforms_to_cache(sg.objects)
        return {'FINISHED'}

class DEL_type_props(bpy.types.PropertyGroup):

    del_type = ["Blender", "3D Slicer", "Both"]
    items = []
    for i in range(len(del_type)):
        item = (str(del_type[i]), str(del_type[i]), str(""), int(i))
        items.append(item)

    Mode: bpy.props.EnumProperty(items=items, description="", default="Blender")

class deleteObjectsBoth(bpy.types.Operator):
    """
    Start updating slicer live by adding a scene_update_post/depsgraph_update_post (2.8) handler
    """
    bl_idname = "link_slicer.delete_objects_both"
    bl_label = "Delete Object(s)"
    
    def execute(self,context):

        if "SlicerLink" not in bpy.data.collections:
            sg = bpy.data.collections.new('SlicerLink')
        else:
            sg = bpy.data.collections['SlicerLink']

        del_mode = bpy.context.scene.DEL_type_props.Mode
        if "Blender" in del_mode:
            for ob in bpy.context.selected_objects:
                #ob.select_set(True) HUH????
                bpy.ops.object.delete(use_global=True, confirm=False)
        elif "3D Slicer" in del_mode:
            if not asyncsock.socket_obj == None:
                packet = ""
                for ob in [(ob, ob.name) for ob in bpy.context.selected_objects if ob.name not in bpy.data.collections.get("ViewLink").objects]:
                    #asyncsock.socket_obj.sock_handler[0].send_data("DEL", ob.name)
                    packet = packet + ob[1] + ","
                    if ob[1] in sg.objects: 
                        sg.objects.unlink(ob[0])
                    write_ob_transforms_to_cache(sg.objects)
                if not packet == "": asyncsock.socket_obj.sock_handler[0].send_data("DEL", packet[:-1])
        elif "Both" in del_mode:
            if not asyncsock.socket_obj == None:
                packet = ""
                for ob in [(ob, ob.name) for ob in bpy.context.selected_objects if ob.name not in bpy.data.collections.get("ViewLink").objects]:
                    #asyncsock.socket_obj.sock_handler[0].send_data("DEL", ob.name)
                    packet = packet + ob[1] + ","
                    if ob[1] in sg.objects: 
                        sg.objects.unlink(ob[0])
                    write_ob_transforms_to_cache(sg.objects)
                if not packet == "": asyncsock.socket_obj.sock_handler[0].send_data("DEL", packet[:-1])
            for ob in bpy.context.selected_objects:
                #context.view_layer.objects.active = ob
                bpy.ops.object.delete(use_global=True, confirm=False)
            
        return {'FINISHED'}

class StopSlicerLink(bpy.types.Operator):
    """
    Stop updating slicer and remove the handler from scene_update_post
    """
    bl_idname = "link_slicer.slicer_link_stop"
    bl_label = "Slicer Link Stop"
    
    def execute(self,context):
        
        handlers = [hand.__name__ for hand in bpy.app.handlers.depsgraph_update_post]
        if "export_to_slicer" in handlers:
            bpy.app.handlers.depsgraph_update_post.remove(export_to_slicer)

        if context.scene.socket_state == "SERVER":
            #try:
            asyncsock.socket_obj.stop_server(asyncsock.socket_obj)
            asyncsock.BlenderComm.stop_thread(asyncsock.thread)
            #except: pass
            asyncsock.socket_obj = None
            context.scene.socket_state = "NONE"
        elif context.scene.socket_state == "CLIENT":
            asyncsock.socket_obj.handle_close()
            context.scene.socket_state = "NONE"
        try: asyncsock.thread.join()
        except: pass
        print("thread joined")
        return {'FINISHED'}        

class AddSliceView(bpy.types.Operator):
    """
    Add slice operator inserts a plane and links it to 3D Slicer. Configures the 3D Slicer panel for additional config. input.
    """
    bl_idname = "link_slicer.add_slice_view"
    bl_label = "Add View"
    
    def execute(self,context):
        if asyncsock.socket_obj is not None:
            for scene in bpy.data.scenes:
                scene.render.engine = 'CYCLES'
                scene.cycles.device = 'GPU'

            bpy.ops.object.select_all(action='DESELECT')

            if bpy.data.objects.get(context.scene.slice_name) is None:
                bpy.ops.mesh.primitive_plane_add(size=50, enter_editmode=True, align='WORLD', location=(0, 0, 0))
                bpy.ops.mesh.select_all(action='DESELECT')
                bpy.ops.object.editmode_toggle()
                bpy.data.objects.get(context.view_layer.objects.active.name).name = context.scene.slice_name + "_transverse_slice"
                bpy.data.objects.get(context.scene.slice_name + "_transverse_slice").data.name = context.scene.slice_name + "_transverse_slice"

                ob = bpy.data.objects.get(context.scene.slice_name + "_transverse_slice") #869
                TRIANGULATE_mod = ob.modifiers.new(name='triangles4slicer_' + ob.name, type="TRIANGULATE")
                bpy.ops.object.modifier_apply(modifier=TRIANGULATE_mod.name)
                #ob.hide_select = True # not possible b/c when resizing the plane we rely on being able to select it in order to reset the origin, when selection is disabled this cannot happen and plane is not centered appropriately

                bpy.ops.object.select_all(action='DESELECT')

                bpy.ops.mesh.primitive_plane_add(size=50, enter_editmode=True, align='WORLD', location=(0, 0, 0))
                bpy.ops.mesh.select_all(action='DESELECT')
                bpy.ops.object.editmode_toggle()
                bpy.data.objects.get(context.view_layer.objects.active.name).name = context.scene.slice_name + "_tangential_slice"
                bpy.data.objects.get(context.scene.slice_name + "_tangential_slice").data.name = context.scene.slice_name + "_tangential_slice"

                ob = bpy.data.objects.get(context.scene.slice_name + "_tangential_slice")
                TRIANGULATE_mod = ob.modifiers.new(name='triangles4slicer_' + ob.name, type="TRIANGULATE")
                bpy.ops.object.modifier_apply(modifier=TRIANGULATE_mod.name)
                #ob.hide_select = True

                bpy.ops.object.select_all(action='DESELECT')

                bpy.ops.mesh.primitive_plane_add(size=50, enter_editmode=True, align='WORLD', location=(0, 0, 0))
                bpy.ops.mesh.select_all(action='DESELECT')
                bpy.ops.object.editmode_toggle()
                bpy.data.objects.get(context.view_layer.objects.active.name).name = context.scene.slice_name + "_freeview_slice"
                bpy.data.objects.get(context.scene.slice_name + "_freeview_slice").data.name = context.scene.slice_name + "_freeview_slice"

                ob = bpy.data.objects.get(context.scene.slice_name + "_freeview_slice")
                TRIANGULATE_mod = ob.modifiers.new(name='triangles4slicer_' + ob.name, type="TRIANGULATE")
                bpy.ops.object.modifier_apply(modifier=TRIANGULATE_mod.name)
                #ob.hide_select = True

                bpy.ops.object.select_all(action='DESELECT')

                #bpy.data.objects[context.scene.slice_name + "_transverse_slice"].hide_select = True
                #bpy.data.objects[context.scene.slice_name + "_tangential_slice"].hide_select = True

            else:
                ShowMessageBox("An object with this name exists. Sending to 3D Slicer.", "Slice View Info:")
            
            
            send_obj_to_slicer([context.scene.slice_name + "_transverse_slice", context.scene.slice_name + "_tangential_slice", context.scene.slice_name + "_freeview_slice"], "ViewLink")
            #time.sleep(1)
            #send_obj_to_slicer([context.scene.slice_name + "_tangential_slice"], "ViewLink")
            #send_obj_to_slicer([context.scene.slice_name + "_freeview_slice"], "ViewLink")
            time.sleep(1)
            #bpy.ops.object.select_all(action='DESELECT')
            asyncsock.socket_obj.sock_handler[0].send_data("SETUP_SLICE", context.scene.slice_name)
            
        return {'FINISHED'}

class DeleteSliceView(bpy.types.Operator):
    """
    Delete slice operator removes selected plane's texture image, material node, and object itself. Resets 3D Slicer UI Panel.
    """
    bl_idname = "link_slicer.delete_slice_view"
    bl_label = "Delete View"
    
    def execute(self,context):
        if asyncsock.socket_obj is not None:
            if bpy.data.objects.get(context.scene.slice_name + "_transverse_slice") is not None and bpy.data.objects.get(context.scene.slice_name + "_tangential_slice") is not None:
                bpy.ops.object.select_all(action='DESELECT')
                for slice in [context.scene.slice_name + "_transverse_slice", context.scene.slice_name + "_tangential_slice"]:
                    for material in bpy.data.objects[slice].data.materials:
                        if material.name in bpy.data.images.keys():
                            bpy.data.images[material.name].user_clear()
                            bpy.data.images.remove(bpy.data.images[material.name])
                        material.user_clear()
                        bpy.data.materials.remove(material)
                    
                    bpy.data.objects[slice].select_set(state=True)

                bpy.context.scene.DEL_type_props.Mode = "Both"
                bpy.ops.link_slicer.delete_objects_both("INVOKE_DEFAULT")
                asyncsock.socket_obj.sock_handler[0].send_data("DEL_SLICE", context.scene.slice_name)

            
        return {'FINISHED'}

class SlicerLinkPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    self_dir = os.path.dirname(os.path.abspath(__file__))
    tmp_dir = os.path.join(self_dir, "slicer_module", "tmp")
    tmp_dir : bpy.props.StringProperty(name="Temp Folder", default=tmp_dir, subtype='DIR_PATH')
    dir_3d_slicer : bpy.props.StringProperty(name = "3D Slicer Location:", description = "Directory path of 3D Slicer for startup.", default = "", subtype='FILE_PATH')
    

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(context.scene, "debug_log")
        row = layout.row()
        row.prop(context.scene, "legacy_sync")
        row.prop(context.scene, "legacy_vertex_threshold")
        row = layout.row()
        row.prop(self, "tmp_dir")
        row = layout.row()
        row.prop(self, "dir_3d_slicer")


class SlicerLinkPanel(bpy.types.Panel):
    """Panel for Slicer LInk"""
    bl_label = "openPlan Control Panel"
    bl_idname = "SCENE_PT_SlicerLinkPanel"
    bl_space_type = "VIEW_3D"
    bl_region_type = 'UI'
    bl_category = "openPlan"
    bl_context = ""

    def draw(self, context):
        layout = self.layout

        scene = context.scene
        '''
        # Create a simple row.
        layout.label(text=" Configure:")

        row = layout.row()
        row.prop(context.scene, "host_addr")
        row = layout.row()
        row.prop(context.scene, "host_port")
        '''
        row = layout.row()
        if context.scene.socket_state == "NONE":
            #row.label(text="Start Mode:")
            #row.operator("link_slicer.slicer_link_server_start") #this may become handy as we scale things to the cloud
            #row.operator("link_slicer.slicer_link_client_start")
            row.prop(context.scene, "DICOM_dir")
            row = layout.row()
            row.operator("link_slicer.slicer_init")
        '''
        elif context.scene.socket_state == "SERVER" or context.scene.socket_state == "CLIENT":
            if context.scene.socket_state == "SERVER": row.label(text="Running: Server mode.")
            elif context.scene.socket_state == "CLIENT":row.label(text="Running: Client mode.")
            row = layout.row()
            row.operator("link_slicer.slicer_link_stop")
        ''' 
        if context.scene.socket_state == "SERVER" or context.scene.socket_state == "CLIENT":
            row = layout.row()
            row = layout.row()
            row.label(text="Object Operators:")

            #row = layout.row()
            #row.operator("object.slicergroup")

            row = layout.row()
            row.operator("link_slicer.link_objects_to_slicer")

            row = layout.row()
            row.operator("link_slicer.unlink_objects_from_slicer")

            row = layout.row()
            #row.operator("link_slicer.delete_objects_both")

            props = bpy.context.scene.DEL_type_props
            row.prop(props, "Mode", text="")
            row.operator("link_slicer.delete_objects_both")
            #row = layout.row()
            #row.prop(context.scene, "delete_slicer")
            row = layout.row()
            row = layout.row()
            row.label(text="Slice View Operators:")
            row = layout.row()
            #row.prop(context.scene, "slice_name")
            row.operator("link_slicer.add_slice_view")
            row = layout.row()
            row.operator("link_slicer.delete_slice_view")

class ModalTimerOperator(bpy.types.Operator):
    """Operator which runs its self from a timer"""
    bl_idname = "wm.modal_timer_operator"
    bl_label = "Modal Timer Operator"

    _timer = None

    def modal(self, context, event):
        if event.type in {'ESC'}:
            self.cancel(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            while not asyncsock.socket_obj.queue.empty():
                try:
                    data = asyncsock.socket_obj.queue.get_nowait()
                    asyncsock.socket_obj.cmd_ops[data[0]](data[1])
                except queue.Empty: continue
                asyncsock.socket_obj.queue.task_done()

            asyncsock.socket_obj.queue.join()

        return {'PASS_THROUGH'}

    def execute(self, context):
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.05, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)

@persistent
def on_load_new(*args):
    if platform.system() == "Windows" and asyncsock.slicer_sysprocess is not None and asyncsock.socket_obj is not None:
        subprocess.call(['taskkill', '/F', '/T', '/PID',  str(asyncsock.slicer_sysprocess.pid)])
        bpy.ops.link_slicer.slicer_link_stop("INVOKE_DEFAULT")
    elif platform.system() == "Darwin" and asyncsock.slicer_sysprocess is not None and asyncsock.socket_obj is not None:
        bpy.ops.link_slicer.slicer_link_stop("INVOKE_DEFAULT")
        asyncsock.slicer_sysprocess.terminate()
        asyncsock.slicer_sysprocess.wait()
        

@persistent
def on_load_post(*args):
    if bpy.context.scene.DICOM_dir != '':
        bpy.context.scene.DICOM_dir = bpy.data.filepath + ".mrb"
        #bpy.ops.link_slicer.slicer_link_stop("INVOKE_DEFAULT")
        bpy.ops.link_slicer.slicer_init("INVOKE_DEFAULT")
    pass

@persistent
def on_save_pre(*args):
    #bpy.context.scene.socket_state = "NONE"
    bpy.context.scene.linked_models.clear()
    if "SlicerLink" in bpy.data.collections:
        for obj in bpy.data.collections['SlicerLink'].objects:
            bpy.context.scene.linked_models.add()
            bpy.context.scene.linked_models[-1].name = obj.name

@persistent
def on_save_post(*args):
    if asyncsock.socket_obj is not None:
        asyncsock.socket_obj.sock_handler[0].send_data("SAVE", bpy.data.filepath)
        #bpy.context.scene.socket_state = "SERVER"

class linked_models_collection(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty( name="Name", description="Linked objects.", default="")

def ShowMessageBox(message = "", title = "Message Box", icon = 'INFO'):

    def draw(self, context):
        self.layout.label(text=message)

    bpy.context.window_manager.popup_menu(draw, title = title, icon = icon)

def register():
    bpy.utils.register_class(Start3DSlicer)
    bpy.utils.register_class(StopSlicerLink)

    bpy.types.Scene.debug_log = bpy.props.BoolProperty(name = "Debug Log", default = True, description = "If True, exception error from asyncsock command executioner on received packet will be logged.")
    bpy.types.Scene.legacy_sync = bpy.props.BoolProperty(name = "File I/O Sync", default = True, description = "If True, large model objects will be exported and imported as files rather than copied over network I/O. Transforms and properties are still over network.")
    bpy.types.Scene.legacy_vertex_threshold = bpy.props.IntProperty(name="Vertex Threshold", description="Legacy IO Vertex Threshold", default=3)

    #register host address, port input, state=NONE/CLIENT/SERVER
    bpy.types.Scene.host_addr = bpy.props.StringProperty(name = "Host", description = "Enter the host PORT the server to listen on OR client to connect to.", default = asyncsock.address[0])
    bpy.types.Scene.host_port = bpy.props.StringProperty(name = "Port", description = "Enter the host PORT the server to listen on OR client to connect to.", default = str(asyncsock.address[1]))
    bpy.types.Scene.socket_state = bpy.props.StringProperty(name="socket_state", default="NONE")

    bpy.types.Scene.overwrite = bpy.props.BoolProperty(name = "Overwrite", default = True, description = "If False, will add objects, if True, will replace entire group with selection")

    bpy.types.Scene.slice_name = bpy.props.StringProperty(name = "Name", description = "Enter the name of the slice view.", default = "view_obj")
    
    bpy.types.Scene.DICOM_dir = bpy.props.StringProperty(name = "", description = "", default = "", subtype='FILE_PATH')
    bpy.utils.register_class(linked_models_collection)
    bpy.types.Scene.linked_models = bpy.props.CollectionProperty(type=linked_models_collection)

    bpy.utils.register_class(SelectedtoSlicerGroup)
    bpy.utils.register_class(StartSlicerLinkServer)
    bpy.utils.register_class(StartSlicerLinkClient)
    bpy.utils.register_class(SlicerLinkPanel)
    bpy.utils.register_class(SlicerLinkPreferences)
    bpy.utils.register_class(linkObjectsToSlicer)
    bpy.utils.register_class(unlinkObjectsFromSlicer)
    bpy.utils.register_class(deleteObjectsBoth)
    bpy.utils.register_class(DEL_type_props)
    bpy.types.Scene.DEL_type_props = bpy.props.PointerProperty(type=DEL_type_props)
    bpy.utils.register_class(AddSliceView)
    bpy.utils.register_class(DeleteSliceView)
    bpy.utils.register_class(ModalTimerOperator)

    
    if not on_load_new in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(on_load_new)
    if not on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load_post)
    if not on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(on_save_pre)
    if not on_save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.append(on_save_post)
    

def unregister():
    del bpy.types.Scene.debug_log
    del bpy.types.Scene.legacy_sync
    del bpy.types.Scene.legacy_vertex_threshold

    if on_load_new in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(on_load_new)
    if on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load_post)
    if on_save_pre in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(on_save_pre)
    if on_save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(on_save_post)

    del bpy.types.Scene.host_addr
    del bpy.types.Scene.host_port
    del bpy.types.Scene.socket_state
    del bpy.types.Scene.overwrite
    del bpy.types.Scene.slice_name

    #del bpy.types.Scene.dir_3d_slicer
    #del bpy.types.Scene.DICOM_dir

    #del bpy.types.Scene.slicer_sysprocess

    bpy.utils.unregister_class(SelectedtoSlicerGroup)
    bpy.utils.unregister_class(StopSlicerLink)
    bpy.utils.unregister_class(StartSlicerLinkServer)
    bpy.utils.unregister_class(StartSlicerLinkClient)
    bpy.utils.unregister_class(Start3DSlicer)
    bpy.utils.unregister_class(SlicerLinkPanel)
    bpy.utils.unregister_class(SlicerLinkPreferences)
    bpy.utils.unregister_class(linkObjectsToSlicer)
    bpy.utils.unregister_class(unlinkObjectsFromSlicer)
    bpy.utils.unregister_class(deleteObjectsBoth)
    bpy.utils.unregister_class(DEL_type_props)
    del bpy.types.Scene.DEL_type_props
    bpy.utils.unregister_class(AddSliceView)
    bpy.utils.unregister_class(DeleteSliceView)
    bpy.utils.unregister_class(ModalTimerOperator)
    
    handlers = [hand.__name__ for hand in bpy.app.handlers.depsgraph_update_post]
    if "export_to_slicer" in handlers:
        bpy.app.handlers.depsgraph_update_post.remove(export_to_slicer)

    """
    handlers = [hand.__name__ for hand in bpy.app.handlers.load_post]
    if "cleanup_temp_dir" in handlers:
        bpy.app.handlers.load_post.remove(cleanup_temp_dir)
    """
    #bpy.utils.unregister_manual_map(SlicerXMLExport)
    #bpy.utils.unregister_manual_map(SlicerPLYExport)
    
if __name__ == "__main__":
    register()