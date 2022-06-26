"""
@author: Patrick R. Moore, Georgi Talmazov

"""

from __main__ import vtk, qt, ctk, slicer

import os
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, Comment, ElementTree, tostring
from xml.etree import ElementTree as ET
import re
import numpy as np
import SurfaceToolbox
from vtk.util.numpy_support import vtk_to_numpy
import ScreenCapture
import time

#http://codeprogress.com/python/libraries/pyqt/showPyQTExample.php?index=419&key=QFileSystemWatcherDirChange&version=4
#http://stackoverflow.com/questions/32097163/pyqt-qfilesystemwatcher-doesnt-capture-the-file-added
#http://codereview.stackexchange.com/questions/104555/directory-watcher-and-notifier-for-files-added-or-removed
#http://codereview.stackexchange.com/questions/104555/directory-watcher-and-notifier-for-files-added-or-removed
#https://github.com/Slicer/Slicer/blob/master/Extensions/Testing/ScriptedLoadableExtensionTemplate/ScriptedLoadableModuleTemplate/ScriptedLoadableModuleTemplate.py

#how to use QTimer
#http://pyqt.sourceforge.net/Docs/PyQt4/qtimer.html
#Endoscopy Thread has QTimer example

from comm import asyncsock

def xor(lst1, lst2):
    """ returns a tuple of items of item not in either of lists
    """
    x = lst2 if len(lst2) > len(lst1) else lst1
    y = lst1 if len(lst1) < len(lst2) else lst2
    return tuple(item for item in x if item not in y)

class BlenderMonitor:
    def __init__(self, parent):
        parent.title = "openPlan"
        parent.categories = ["Dentistry"]
        parent.dependencies = []
        parent.contributors = ["Patrick Moore", "Georgi Talmazov (Dental Software Foundation)"] # replace with "Firstname Lastname (Org)"
        parent.helpText = """
        Example of scripted loadable extension for the HelloPython tutorial that monitors a directory for file changes.
        """
        parent.acknowledgementText = """Independently developed for the good of the world""" # replace with organization, grant and thanks.
        self.parent = parent

#
# qHelloPythonWidget
#

class BlenderMonitorWidget:
    def __init__(self, parent = None):
        if not parent:
            self.parent = slicer.qMRMLWidget()
            self.parent.setLayout(qt.QVBoxLayout())
            self.parent.setMRMLScene(slicer.mrmlScene)
        else:
            self.parent = parent
        self.layout = self.parent.layout()
        if not parent:
            self.setup()
            self.parent.show()

        self.watching = True #False before but now we are automating things
        self.sock = None
        self.host_address = asyncsock.address[0]
        self.host_port = asyncsock.address[1]
        self.sliceSock = None
        self.SlicerSelectedModelsList = []
        #slice list
        self.sliceViewCache = {}
        self.slicer_3dview = False
        self.workingVolume = None

        self.legacy_sync = None #True
        self.legacy_vertex_threshold = None #30000
        self_dir = os.path.dirname(os.path.abspath(__file__))
        self.tmp_dir = os.path.join(self_dir, "tmp")
        
    def setup(self):
        # Instantiate and connect widgets ...
        
        # Collapsible button
        sampleCollapsibleButton = ctk.ctkCollapsibleButton()
        sampleCollapsibleButton.text = "Configuration:"
        self.layout.addWidget(sampleCollapsibleButton)

        # Layout within the sample collapsible button
        self.sampleFormLayout = qt.QFormLayout(sampleCollapsibleButton)
        self.log_debug = qt.QCheckBox()
        #self.log_debug.setText("Debug: ")
        self.log_debug.setChecked(True)
        self.sampleFormLayout.addRow("Debug:", self.log_debug)

        #Models list
        addModelButton = qt.QPushButton("Add Model")
        addModelButton.toolTip = "Add a model to the list to sync with Blender."
        self.sampleFormLayout.addRow(addModelButton)
        addModelButton.connect('clicked()', self.onaddModelButtonToggled)

    def config_layout(self, slicer_3dview):
        self.slicer_3dview = slicer_3dview
        customLayoutId = 501
        if self.slicer_3dview:
            view_mode = """
            <view class="vtkMRMLViewNode" singletontag="3D">
             <property name="viewlabel" action="default">3D</property>
            </view>
            """
        else:
            view_mode = """
            <view class="vtkMRMLSliceNode" singletontag="2D" verticalStretch="0">
             <property name="orientation" action="default">Reformat</property>
             <property name="viewlabel" action="default">P</property>
             <property name="viewcolor" action="default">#000000</property>
            </view>
            """
        XML_layout = """
        <layout type="vertical" split="true" >
         <item splitSize="500">
          <layout type="horizontal">
           <item>
            <view class="vtkMRMLSliceNode" singletontag="Red">
             <property name="orientation" action="default">Axial</property>
             <property name="viewlabel" action="default">R</property>
             <property name="viewcolor" action="default">#F34A33</property>
            </view>
           </item>
           <item>
            <view class="vtkMRMLSliceNode" singletontag="view_transverse_slice">
             <property name="orientation" action="default">Coronal</property>
             <property name="viewlabel" action="default">G</property>
             <property name="viewcolor" action="default">#6EB04B</property>
            </view>
           </item>
           <item>
            <view class="vtkMRMLSliceNode" singletontag="view_tangential_slice">
             <property name="orientation" action="default">Sagittal</property>
             <property name="viewlabel" action="default">Y</property>
             <property name="viewcolor" action="default">#EDD54C</property>
            </view>
           </item>
          </layout>
         </item>
         <item splitSize="500">
          <layout type="horizontal">
           <item>
           %s
           </item>
           <item>
            <view class="vtkMRMLSliceNode" singletontag="view_freeview_slice">
             <property name="orientation" action="default">Axial</property>
             <property name="viewlabel" action="default">F</property>
             <property name="viewcolor" action="default">#EDD54C</property>
            </view>
           </item>
          </layout>
         </item>
        </layout>
        """%view_mode
        lm = slicer.app.layoutManager()
        lm.layoutLogic().GetLayoutNode().AddLayoutDescription(customLayoutId, XML_layout)
        lm.setLayout(customLayoutId)

    def connect_to_blender(self, host, port):
        self.host_address = host
        self.host_port = port
        self.sock = asyncsock.SlicerComm.EchoClient(str(self.host_address), int(self.host_port), [("XML", self.update_scene), ("OBJ", self.import_obj_from_blender), ("OBJ_MULTIPLE", self.import_multiple), ("CHECK", self.obj_check_handle), ("DEL", self.delete_model), ("SETUP_SLICE", self.add_slice_view), ("DEL_SLICE", self.delete_slice_view), ("FILE_OBJ", self.FILE_import_obj_from_blender), ("FILE_OBJ_MULTIPLE", self.FILE_import_multiple), ("CONFIG_PARAMS", self.blender_config_params), ("VIEW_UPDATE", self.slice_view_update_scene), ("SAVE", self.save_project)], self.log_debug.isChecked())

    def onaddModelButtonToggled(self): #, select = None):
        for model in self.SlicerSelectedModelsList:
            if model[0] == None and model[2] == "NEW":
                return
        # https://python.hotexamples.com/examples/slicer/-/qMRMLNodeComboBox/python-qmrmlnodecombobox-function-examples.html
        modelNodeSelector = slicer.qMRMLNodeComboBox()
        modelNodeSelector.objectName = 'modelNodeSelector'
        modelNodeSelector.toolTip = "Select a model."
        modelNodeSelector.nodeTypes = ['vtkMRMLModelNode']
        modelNodeSelector.noneEnabled = True
        modelNodeSelector.addEnabled = True
        modelNodeSelector.removeEnabled = True
        #if select is not None:
        #    modelNodeSelector.currentNodeID = select
        modelNodeSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.obj_check_send)
        self.sampleFormLayout.addRow(modelNodeSelector)

        self.parent.connect('mrmlSceneChanged(vtkMRMLScene*)', modelNodeSelector, 'setMRMLScene(vtkMRMLScene*)')
        modelNodeSelector.setMRMLScene(slicer.mrmlScene)
        
        self.SlicerSelectedModelsList.append([None , modelNodeSelector, "NEW"])
        #print(self.SlicerSelectedModelsList)

    def update_scene(self, xml):
        if not self.watching: return

        try: #any better ideas??
            tree = ET.ElementTree(ET.fromstring(xml))
        except:
            return
        x_scene = tree.getroot()
        
        s_scene = slicer.mrmlScene
        #scene = slicer.mrmlScene
        for b_ob in x_scene:
            #get the name of blender object
            name = b_ob.get('name')
                    
            
            xml_mx = b_ob.find('matrix')
            try:
                slicer_model = slicer.util.getNode(name)
            except slicer.util.MRMLNodeNotFoundException:
                slicer_model = None
                return
            
            #if not slicer_model:
            #try to get transform node
            try:
                transform = slicer.util.getNode(name+'_trans')
            except slicer.util.MRMLNodeNotFoundException:
                transform = None

            try:
                pano_model_trans = slicer.util.getNode(name+'_pano_trans')
            except slicer.util.MRMLNodeNotFoundException:
                pano_model_trans = None
                
            if not transform:
                transform = slicer.vtkMRMLTransformNode()
                transform.SetName(name+'_trans')        
                s_scene.AddNode(transform)
            
            slicer_model.SetAndObserveTransformNodeID(transform.GetID())
        
            #set the elements of transform form the matrix
            #my_matrix = vtk.vtkMatrix4x4()
            my_matrix = transform.GetMatrixTransformFromParent()
            for i in range(0,4):
                for j in range(0,4):
                    my_matrix.SetElement(i,j,float(xml_mx[i][j].text))
        
            #update object location in scene
            transform.SetAndObserveMatrixTransformToParent(my_matrix)

            if pano_model_trans: pano_model_trans.SetAndObserveMatrixTransformToParent(my_matrix)

            #update color
            if b_ob.find("material"):
                mat_color = b_ob.find('material')
                slicer_model.GetDisplayNode().SetColor(float(mat_color.find('r').text), float(mat_color.find('g').text), float(mat_color.find('b').text))
                slicer_model.GetDisplayNode().SetOpacity(float(mat_color.find('a').text))
                #modelDisplayNode=slicer.mrmlScene.AddNewNodeByClass("vtkMRMLModelDisplayNode")
                #modelDisplayNode.SetViewNodeIDs([slicer.mrmlScene.GetFirstNodeByName("View1").GetID()])
                #modelDisplayNode.SetColor(float(mat_color.find('r').text), float(mat_color.find('g').text), float(mat_color.find('b').text))
                #modelDisplayNode.SetOpacity(float(mat_color.find('a').text))
                #slicer_model.AddAndObserveDisplayNodeID(modelDisplayNode.GetID())
            
            #permanently apply transform - does not seem to work in live mode
            #logic = slicer.vtkSlicerTransformLogic()
            #logic.hardenTransform(slicer_model)

            #disp_node = slicer_model.GetDisplayNode()
            #disp_node.SetSliceIntersectionVisibility(True)
            #disp_node.SetSliceIntersectionThickness(2)

    def update_scene_blender(self, modelNode, sock = None, group = 'SlicerLink'):
        #print(tostring(self.build_xml_scene(modelNode.GetName())).decode())
        if sock == None: sock = self.sock
        sock.send_data("XML", tostring(self.build_xml_scene(modelNode.GetName(), group)).decode())
        #self.sock.send_data("CHECK", "UNLINK_BREAK_" + modelNode.GetName())

    def obj_check_handle(self, data):
        status, obj_name = data.split("_BREAK_")
        if status == "MISSING":
            self.send_model_to_blender(slicer.util.getNode(obj_name))
        elif status == "NOT LINKED":
            self.sock.send_data("CHECK", "LINK_BREAK_" + obj_name)
            #self.onaddModelButtonToggled()
        elif status == "LINKED":
            slicer.util.confirmOkCancelDisplay("Object already linked.", "openPlan Info:")
        elif status == "UNLINK":
            for model in self.SlicerSelectedModelsList:
                if model[0] == obj_name:
                    model[1].deleteLater()
                    self.SlicerSelectedModelsList.remove(model)

        elif status == "STATUS":
            try:
                slicer.util.getNode(obj_name)
                self.sock.send_data("CHECK", "LINK_BREAK_" + obj_name)
            except slicer.util.MRMLNodeNotFoundException:
                self.sock.send_data("CHECK", "MISSING_BREAK_" + obj_name)
        elif status == "STATUS_MULTIPLE":
            obj_name = obj_name.split(",")
            #print(obj_name)
            missing_objs = ""
            unlinked_objs = ""
            for obj in obj_name:
                try:
                    slicer.util.getNode(obj)
                    unlinked_objs = unlinked_objs + obj + ","
                except slicer.util.MRMLNodeNotFoundException:
                    missing_objs = missing_objs + obj + ","
            if not unlinked_objs == "" and missing_objs == "":
                self.sock.send_data("CHECK", "LINK_MULTIPLE_BREAK_" + unlinked_objs[:-1])
            elif not missing_objs == "" and unlinked_objs == "":
                self.sock.send_data("CHECK", "MISSING_MULTIPLE_BREAK_" + missing_objs[:-1])
            elif not missing_objs == "" and not unlinked_objs == "":
                self.sock.send_data("CHECK", "LINK+MISSING_MULTIPLE_BREAK_" + unlinked_objs[:-1] + ";" + missing_objs[:-1])

    def obj_check_send(self, modelNodeSelectorObj):
        #modelNode = modelNodeSelectorObj
        if modelNodeSelectorObj is not None:
            for model in self.SlicerSelectedModelsList:
                if model[0] == None and model[2] == "NEW":
                    self.SlicerSelectedModelsList[self.SlicerSelectedModelsList.index(model)][0] = modelNodeSelectorObj.GetName()
                    self.SlicerSelectedModelsList[self.SlicerSelectedModelsList.index(model)][2] = ""

            #slicer.util.confirmOkCancelDisplay("Checking object.", "openPlan Info:")

            model_name = modelNodeSelectorObj.GetName()
            self.sock.send_data("CHECK", "STATUS_BREAK_" + model_name)
        else:
            for model in self.SlicerSelectedModelsList:
                if model[1].currentNode() == None and model[0] is not None:
                    self.sock.send_data("CHECK", "UNLINK_BREAK_" + model[0])
                    model[1].deleteLater()
                    self.SlicerSelectedModelsList.remove(model)
                    #print(self.SlicerSelectedModelsList)
                    return

    def delete_model(self, obj_name):
        obj_name = obj_name.split(",")
        for model in self.SlicerSelectedModelsList:
            if model[0] in obj_name:
                model[1].deleteLater()
                self.SlicerSelectedModelsList.remove(model)
        for model in obj_name:
            try: slicer.mrmlScene.RemoveNode(slicer.util.getNode(model))
            except: pass
            try: slicer.mrmlScene.RemoveNode(slicer.util.getNode(model.GetName()+"_straightened"))
            except: pass

    def send_model_to_blender(self, modelNodeSelector):
        if not self.SlicerSelectedModelsList == []:
            modelNode = modelNodeSelector

            if len(slicer.util.arrayFromModelPoints(modelNode).tolist()) > 300000: #this can be fine tuned, lower for speed, 300,000 is optimal for geo preserve
                #print(len(slicer.util.arrayFromModelPoints(modelNode).tolist()))
                SFT_logic = SurfaceToolbox.SurfaceToolboxLogic()
                
                def setDefaultParameters(parameterNode):
                    """
                    Initialize parameter node with default settings.
                    """
                    defaultValues = [
                    ("decimation", "true"),
                    ("decimationReduction", "0.95"),
                    ("decimationBoundaryDeletion", "true"),
                    ("smoothing", "false"),
                    ("smoothingMethod", "Laplace"),
                    ("smoothingLaplaceIterations", "100"),
                    ("smoothingLaplaceRelaxation", "0.5"),
                    ("smoothingTaubinIterations", "30"),
                    ("smoothingTaubinPassBand", "0.1"),
                    ("smoothingBoundarySmoothing", "true"),
                    ("normals", "false"),
                    ("normalsAutoOrient", "false"),
                    ("normalsFlip", "false"),
                    ("normalsSplitting", "false"),
                    ("normalsFeatureAngle", "30.0"),
                    ("mirror", "false"),
                    ("mirrorX", "false"),
                    ("mirrorY", "false"),
                    ("mirrorZ", "false"),
                    ("cleaner", "false"),
                    ("fillHoles", "false"),
                    ("fillHolesSize", "1000.0"),
                    ("connectivity", "false"),
                    ("scale", "false"),
                    ("scaleX", "0.5"),
                    ("scaleY", "0.5"),
                    ("scaleZ", "0.5"),
                    ("translate", "false"),
                    ("translateX", "0.0"),
                    ("translateY", "0.0"),
                    ("translateZ", "0.0"),
                    ("relax", "false"),
                    ("relaxIterations", "5"),
                    ("bordersOut", "false"),
                    ("translateCenterToOrigin", "false")
                    ]
                    for parameterName, defaultValue in defaultValues:
                        if not parameterNode.GetParameter(parameterName):
                            parameterNode.SetParameter(parameterName, defaultValue)

                def updateProcess(value):
                    """Display changing process value"""
                    return

                                
                try: parameterNode = slicer.util.getNode("model_filter")
                except slicer.util.MRMLNodeNotFoundException:
                    parameterNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScriptedModuleNode")
                    parameterNode.SetName("model_filter")

                setDefaultParameters(parameterNode)
                parameterNode.SetNodeReferenceID("inputModel", modelNode.GetID())
                parameterNode.SetNodeReferenceID("outputModel", modelNode.GetID())

                SFT_logic.updateProcessCallback = updateProcess
                result = SFT_logic.applyFilters(parameterNode) #, updateProcess)
                
                slicer.app.processEvents()

            if self.legacy_sync == True and len(slicer.util.arrayFromModelPoints(modelNode).tolist()) > self.legacy_vertex_threshold:
                print(modelNode.GetName())
                modelDisplayNode = modelNode.GetDisplayNode()
                triangles = vtk.vtkTriangleFilter()
                triangles.SetInputConnection(modelDisplayNode.GetOutputPolyDataConnection())

                plyWriter = vtk.vtkPLYWriter()
                plyWriter.SetInputConnection(triangles.GetOutputPort())
                lut = vtk.vtkLookupTable()
                #lut.DeepCopy(modelDisplayNode.GetColorNode().GetLookupTable()) #color
                lut.SetRange(modelDisplayNode.GetScalarRange())
                plyWriter.SetLookupTable(lut)
                plyWriter.SetArrayName(modelDisplayNode.GetActiveScalarName())

                plyWriter.SetFileName(os.path.join(self.tmp_dir, modelNode.GetName()+".ply"))
                plyWriter.Write()
                #time.sleep(5)
                self.sock.send_data("FILE_OBJ", tostring(self.build_xml_scene(modelNode.GetName(), 'SlicerLink')).decode())

            else:
                #.currentNode()
                #print(len(slicer.util.arrayFromModelPoints(modelNode).tolist()))
                modelNode.CreateDefaultDisplayNodes()
                model_points = str(slicer.util.arrayFromModelPoints(modelNode).tolist())
                model_polys = str(self.arrayFromModelPolys(modelNode).tolist())
                packet = "%s_POLYS_%s_XMLDATA_%s"%(model_points, model_polys, tostring(self.build_xml_scene(modelNode.GetName(), 'SlicerLink')).decode())
                #print(model_polys)
                #print(packet)
                slicer.util.confirmOkCancelDisplay("Sending object to Blender.", "openPlan Info:")

                self.sock.send_data("OBJ", packet)

    def arrayFromModelPolys(self, modelNode):
        """Return point positions of a model node as numpy array.
        Point coordinates can be modified by modifying the numpy array.
        After all modifications has been completed, call :py:meth:`arrayFromModelPointsModified`.
        .. warning:: Important: memory area of the returned array is managed by VTK,
            therefore values in the array may be changed, but the array must not be reallocated.
            See :py:meth:`arrayFromVolume` for details.
        """
        #import vtk.util.numpy_support
        pointData = modelNode.GetPolyData().GetPolys().GetData()
        narray = vtk.util.numpy_support.vtk_to_numpy(pointData)
        return narray

    def material_to_xml_element(self, nodeName):
        rgb_color = slicer.util.getNode(nodeName).GetDisplayNode().GetColor()
        alpha = slicer.util.getNode(nodeName).GetDisplayNode().GetOpacity()

        xml_mat = Element('material')

        r = SubElement(xml_mat, 'r')
        r.text = str(round(rgb_color[0],4))
        g = SubElement(xml_mat, 'g')
        g.text = str(round(rgb_color[1],4))
        b = SubElement(xml_mat, 'b')
        b.text = str(round(rgb_color[2],4))
        a = SubElement(xml_mat, 'a')
        a.text = str(round(alpha,2))
        
        return xml_mat
    
    def matrix_to_xml_element(self, mx):
        nrow = len(mx)
        ncol = len(mx[0])
        
        xml_mx = Element('matrix')
        
        for i in range(0,nrow):
            xml_row = SubElement(xml_mx, 'row')
            for j in range(0,ncol):
                mx_entry = SubElement(xml_row, 'entry')
                mx_entry.text = str(mx[i][j])
                
        return xml_mx

    def build_xml_scene(self, nodeName, group):
        '''
        obs - list of slicer objects
        file - filepath to write the xml
        builds the XML scene of all object in self.SlicerSelectedModelsList
        '''
            
        x_scene = Element('scene')

        s_scene = slicer.mrmlScene
        if slicer.util.getNode(nodeName) is not None:
            model = slicer.util.getNode(nodeName)
            try:
                transform = slicer.util.getNode(model.GetName()+'_trans')
            except slicer.util.MRMLNodeNotFoundException:
                transform = None
                
            if not transform:
                transform = slicer.vtkMRMLTransformNode()
                transform.SetName(model.GetName()+'_trans')        
                s_scene.AddNode(transform)
            model.SetAndObserveTransformNodeID(transform.GetID())

            xob = SubElement(x_scene, 'b_object')
            xob.set('name', model.GetName())
            xob.set('group', group)
            

            my_matrix = transform.GetMatrixTransformToParent()
            xmlmx = self.matrix_to_xml_element(slicer.util.arrayFromVTKMatrix(my_matrix))
            xob.extend([xmlmx])

            xmlmat = self.material_to_xml_element(nodeName)
            xob.extend([xmlmat])
                        
        return x_scene

    def import_multiple(self, data):
        objects = data.split("_N_OBJ_")
        for obj in objects:
            self.import_obj_from_blender(obj)

    def FILE_import_multiple(self, data):
        objects = data.split("_XMLDATA_")
        #print(data)
        #print(objects)
        for object_xml in objects:
            self.FILE_import_obj_from_blender(object_xml)

    def import_obj_from_blender(self, data):
        #slicer.util.confirmOkCancelDisplay("Received object(s) from Blender.", "openPlan Info:")
        def mkVtkIdList(it):
            vil = vtk.vtkIdList()
            for i in it:
                vil.InsertNextId(int(i))
            return vil
        #print(data)
        obj, xml = data.split("_XMLDATA_")
        obj_points, obj_polys = obj.split("_POLYS_")
        obj_points = eval(obj_points)
        obj_polys = eval(obj_polys)
        blender_faces = []
        offset = 0 #unflatten the list from blender
        while ( offset < len(obj_polys)):
            vertices_per_face = obj_polys[offset]
            offset += 1
            vertex_indices = obj_polys[offset : offset + vertices_per_face]
            blender_faces.append(vertex_indices)
            offset += vertices_per_face
        tree = ET.ElementTree(ET.fromstring(xml))
        x_scene = tree.getroot()

        try:
            slicer.util.getNode(x_scene[0].get('name'))
            if x_scene[0].get('group') == "ViewLink":
                self.delete_model(x_scene[0].get('name'))
        except slicer.util.MRMLNodeNotFoundException: pass

        mesh = vtk.vtkPolyData()
        points = vtk.vtkPoints()
        polys = vtk.vtkCellArray()
        #print(blender_faces)
        for i in range(len(obj_points)):
            points.InsertPoint(i, obj_points[i])
        for i in range(len(blender_faces)):
            polys.InsertNextCell(mkVtkIdList(blender_faces[i]))
        mesh.SetPoints(points)
        mesh.SetPolys(polys)

        # Create model node and add to scene
        modelNode = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLModelNode')
        modelNode.SetName(x_scene[0].get('name')) #only expecting one obj in the xml, since sent w/ OBJ together
        modelNode.SetAndObservePolyData(mesh)
        modelNode.CreateDefaultDisplayNodes()
        modelNode.GetDisplayNode().SetSliceIntersectionVisibility(True)
        modelNode.GetDisplayNode().SetSliceIntersectionThickness(2)
        
        if((bool(self.sliceViewCache)) and (x_scene[0].get('name') in [slice+"_transverse_slice" for slice in self.sliceViewCache.keys()] or x_scene[0].get('name') in [slice+"_tangential_slice" for slice in self.sliceViewCache.keys()])):
            if self.slicer_3dview:
                modelNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed', 'vtkMRMLViewNode3D'))
            else:
                modelNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed',))
        else:
            if self.slicer_3dview:
                modelNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed','vtkMRMLSliceNodeview_transverse_slice', 'vtkMRMLSliceNodeview_tangential_slice', "vtkMRMLSliceNodeview_freeview_slice", 'vtkMRMLViewNode3D'))
            else:
                modelNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed','vtkMRMLSliceNodeview_transverse_slice', 'vtkMRMLSliceNodeview_tangential_slice', "vtkMRMLSliceNodeview_freeview_slice",))


        #update object location in scene
        self.update_scene(xml)

        #self.SlicerSelectedModelsList.append([modelNodeSelector.currentNode().GetName(), modelNodeSelector, ""])

        #TODO: apply the incoming xml matrix data to the newly imported object right away, dont wait for the event from blender

    def FILE_import_obj_from_blender(self, data):
        tree = ET.ElementTree(ET.fromstring(data))
        x_scene = tree.getroot()

        try:
            slicer.util.getNode(x_scene[0].get('name'))
            if x_scene[0].get('group') == "ViewLink":
                self.delete_model(x_scene[0].get('name'))
        except slicer.util.MRMLNodeNotFoundException: pass

        ply_reader = vtk.vtkPLYReader()
        ply_reader.SetFileName(os.path.join(self.tmp_dir, x_scene[0].get('name')+".ply"))
        ply_reader.Update()
        polydata = ply_reader.GetOutput()
        modelNode = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLModelNode')
        modelNode.SetName(x_scene[0].get('name')) #only expecting one obj in the xml, since sent w/ OBJ together
        modelNode.SetAndObservePolyData(polydata)
        modelNode.CreateDefaultDisplayNodes()
        modelNode.GetDisplayNode().SetSliceIntersectionVisibility(True)
        modelNode.GetDisplayNode().SetSliceIntersectionThickness(2)

        if((bool(self.sliceViewCache)) and (x_scene[0].get('name') in [slice+"_transverse_slice" for slice in self.sliceViewCache.keys()] or x_scene[0].get('name') in [slice+"_tangential_slice" for slice in self.sliceViewCache.keys()])):
            if self.slicer_3dview:
                modelNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed', 'vtkMRMLViewNode3D'))
            else:
                modelNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed',))
        else:
            if self.slicer_3dview:
                modelNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed','vtkMRMLSliceNodeview_transverse_slice', 'vtkMRMLSliceNodeview_tangential_slice', "vtkMRMLSliceNodeview_freeview_slice", 'vtkMRMLViewNode3D'))
            else:
                modelNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed','vtkMRMLSliceNodeview_transverse_slice', 'vtkMRMLSliceNodeview_tangential_slice', "vtkMRMLSliceNodeview_freeview_slice",))

        self.update_scene(data)

        os.remove(os.path.join(self.tmp_dir, x_scene[0].get('name') + ".ply"))

    def blender_config_params(self, data):
        params = eval(data)
        self.legacy_sync = params["legacy_sync"]
        self.legacy_vertex_threshold = params["legacy_vertex_threshold"]
        #self.tmp_dir = params["tmp_dir"]

    def add_slice_view(self, name):

        class sliceViewPanel():
            def __init__(self, name, widgetClass, layout, parent):
                self.widgetClass = widgetClass
                #self.sliceViewLayout = sliceViewLayout
                self.parent = parent
                self.curvePoints = None
                self.curveNode = None
                #self.selectedView = None
                self.plane_model = name
                self.f = None
                self.slider_event = False

                self.sliceSock = asyncsock.SlicerComm.EchoClient(str(self.widgetClass.host_address), int(self.widgetClass.host_port), [("XML", self.widgetClass.update_scene), ("OBJ", self.widgetClass.import_obj_from_blender), ("OBJ_MULTIPLE", self.widgetClass.import_multiple), ("CHECK", self.widgetClass.obj_check_handle), ("DEL", self.widgetClass.delete_model), ("SETUP_SLICE", self.widgetClass.add_slice_view), ("DEL_SLICE", self.widgetClass.delete_slice_view)])
                
                sliceViewSettings = ctk.ctkCollapsibleButton()
                sliceViewSettings.text = "Slice View Settings: " + name
                layout.addWidget(sliceViewSettings)
                self.sliceViewLayout = qt.QFormLayout(sliceViewSettings)
                self.sliceViewSettings = sliceViewSettings
                
                #name_disp = qt.QLineEdit()
                #name_disp.setText(name)
                #self.name_disp = name_disp
                #self.sliceViewLayout.addRow("Slice:", name_disp)

                """
                sliceNodeSelector = slicer.qMRMLNodeComboBox()
                sliceNodeSelector.objectName = 'sliceNodeSelector'
                sliceNodeSelector.toolTip = "Select a viewing slice."
                sliceNodeSelector.nodeTypes = ['vtkMRMLSliceNode']
                sliceNodeSelector.noneEnabled = True
                sliceNodeSelector.addEnabled = True
                sliceNodeSelector.removeEnabled = True
                #if select is not None:
                #    modelNodeSelector.currentNodeID = select
                sliceNodeSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.view_node)
                self.sliceViewLayout.addRow(sliceNodeSelector)

                self.parent.connect('mrmlSceneChanged(vtkMRMLScene*)', sliceNodeSelector, 'setMRMLScene(vtkMRMLScene*)')
                sliceNodeSelector.setMRMLScene(slicer.mrmlScene)
                self.sliceNodeSelector = sliceNodeSelector
                """

                # Input fiducials node selector
                inputFiducialsNodeSelector = slicer.qMRMLNodeComboBox()
                inputFiducialsNodeSelector.objectName = 'inputFiducialsNodeSelector'
                inputFiducialsNodeSelector.toolTip = "Select a fiducial list to define control points for the path."
                inputFiducialsNodeSelector.nodeTypes = ['vtkMRMLMarkupsCurveNode']
                inputFiducialsNodeSelector.noneEnabled = True
                inputFiducialsNodeSelector.addEnabled = True
                inputFiducialsNodeSelector.removeEnabled = True
                inputFiducialsNodeSelector.connect('currentNodeChanged(vtkMRMLNode*)', self.curve_node)
                self.sliceViewLayout.addRow("Input Curve:", inputFiducialsNodeSelector)
                self.parent.connect('mrmlSceneChanged(vtkMRMLScene*)',
                                    inputFiducialsNodeSelector, 'setMRMLScene(vtkMRMLScene*)')

                inputFiducialsNodeSelector.setMRMLScene(slicer.mrmlScene)
                self.inputFiducialsNodeSelector = inputFiducialsNodeSelector
                
                # Frame slider
                self.frameSlider = ctk.ctkSliderWidget()
                self.frameSlider.connect('valueChanged(double)', self.transverseStep)
                self.frameSlider.decimals = 0
                self.sliceViewLayout.addRow("Transverse:", self.frameSlider)

                # Slice rotate slider
                self.rotateView = ctk.ctkSliderWidget()
                self.rotateView.connect('valueChanged(double)', self.tangentialAngle)
                self.rotateView.decimals = 0
                self.rotateView.maximum = 360
                self.sliceViewLayout.addRow("Tangential:", self.rotateView)

                #Freeview slice sliders
                self.freeviewCollapsibleButton = ctk.ctkCollapsibleButton()
                self.freeviewCollapsibleButton.text = "Free View Controls"
                self.freeviewCollapsibleButton.enabled = True #originally FALSE
                layout.addWidget(self.freeviewCollapsibleButton)

                # Layout within the Flythrough collapsible button
                freeviewFormLayout = qt.QFormLayout(self.freeviewCollapsibleButton)
                # Frame slider
                self.fv_tan_slider = ctk.ctkSliderWidget()
                self.fv_tan_slider.connect('valueChanged(double)', self.freeViewAngles)
                self.fv_tan_slider.decimals = 0
                self.fv_tan_slider.maximum = 360
                freeviewFormLayout.addRow("Tangential Angle:", self.fv_tan_slider)

                # Slice rotate slider
                self.fv_ax_slider = ctk.ctkSliderWidget()
                self.fv_ax_slider.connect('valueChanged(double)', self.freeViewAngles)
                self.fv_ax_slider.decimals = 0
                self.fv_ax_slider.maximum = 360
                freeviewFormLayout.addRow("Axial Angle:", self.fv_ax_slider)

                if self.widgetClass.slicer_3dview == False:
                    label = qt.QLabel()
                    label.text = ""
                    self.sliceViewLayout.addRow("Pantomograph ROI", label)

                    self.curve_res = qt.QLineEdit()
                    self.curve_res.setText(1.0)
                    self.slice_res = qt.QLineEdit()
                    self.slice_res.setText(0.5)
                    self.sliceViewLayout.addRow("Curve Resolution:", self.curve_res)
                    self.sliceViewLayout.addRow("Slice Resolution:", self.slice_res)

                    self.pano_x = qt.QLineEdit()
                    self.pano_x.setText(100)
                    self.pano_y = qt.QLineEdit()
                    self.pano_y.setText(25)
                    self.sliceViewLayout.addRow("Slice Height (mm):", self.pano_x)
                    self.sliceViewLayout.addRow("Slice Width (mm):", self.pano_y)

                    # Build Pantomograph button
                    self.PantomographButton = qt.QPushButton("Show Pantomograph")
                    self.PantomographButton.toolTip = "Show pantomograph from selected curve path."
                    self.sliceViewLayout.addRow(self.PantomographButton)
                    self.PantomographButton.connect('clicked()', self.onPantomographButtonToggled)

                    self.normal_angle = ctk.ctkSliderWidget()
                    self.normal_angle.connect('valueChanged(double)', self.rotate_normal)
                    #self.normal_angle.decimals = 0.0
                    #self.normal_angle.singleStep = 1
                    self.normal_angle.minimum = 0
                    self.normal_angle.maximum = 360
                    self.sliceViewLayout.addRow("View Angle:", self.normal_angle)

                slicer.util.getNode(self.plane_model + "_transverse_slice").GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed',)) #('vtkMRMLViewNode1', 'vtkMRMLSliceNodeRed', 'vtkMRMLSliceNodeGreen', 'vtkMRMLSliceNodeYellow')
                #slicer.util.getNode(self.plane_model + "_transverse_slice").GetDisplayNode().Modified()
                slicer.util.getNode(self.plane_model + "_tangential_slice").GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed',))
                #slicer.util.getNode(self.plane_model + "_tangential_slice").GetDisplayNode().Modified()
                slicer.util.getNode(self.plane_model + "_freeview_slice").GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed',))

                self.slice_dims_buff = {self.plane_model + "_transverse_slice" : None,
                                        self.plane_model + "_tangential_slice" : None,
                                        self.plane_model + "_freeview_slice" : None
                                        }
                
                #self.widgetClass.slice_view_numpy("Green", self.plane_model + "_transverse", self.sliceSock, mode="NEW")
                #self.sliceSock.send_data("SELECT_OBJ", self.plane_model + "_transverse")
                #time.sleep(1)
                
                #self.widgetClass.slice_view_numpy("Yellow", self.plane_model + "_tangential", self.widgetClass.sock, mode="NEW")
                #self.widgetClass.sock.send_data("SELECT_OBJ", self.plane_model + "_tangential")

                #for sliceNodeId in ['vtkMRMLSliceNodeRed', 'vtkMRMLSliceNodeGreen', 'vtkMRMLSliceNodeYellow', "vtkMRMLSliceNodeFreeView"]:
                #    slicer.mrmlScene.GetNodeByID(sliceNodeId).AddObserver(vtk.vtkCommand.ModifiedEvent, self.sliceNodeTransform)

            def get_slice_img_dims(self, sliceNodeID):
                sliceNodeID = 'vtkMRMLSliceNode%s'%sliceNodeID
                # Get image data from slice view
                sliceNode = slicer.mrmlScene.GetNodeByID(sliceNodeID)
                viewNodeID = sliceNodeID
                cap = ScreenCapture.ScreenCaptureLogic()
                view = cap.viewFromNode(slicer.mrmlScene.GetNodeByID(viewNodeID))
                # Capture single view
                rw = view.renderWindow()
                wti = vtk.vtkWindowToImageFilter()
                wti.SetInput(rw)
                wti.Update()
                capturedImage = wti.GetOutput()
                return capturedImage.GetDimensions()

            def reslice_on_path(self, p0, pN, viewNode, planeNode, aspectRatio = None, rotateZ = None, rotateT = None):
                #print(viewNode)
                fx=np.poly1d(np.polyfit([p0[0],pN[0]],[p0[1],pN[1]], 1))
                fdx = np.polyder(fx)
                normal_line = lambda x: (-1/fdx(p0[0]))*(x-p0[0])+p0[1]
                t=np.array([p0[0]+1,normal_line(p0[0]+1),p0[2]], dtype='f')
                t=t-p0
                n=pN-p0
                t.astype(float)
                n.astype(float)
                p0.astype(float)
                sliceNode = slicer.app.layoutManager().sliceWidget(viewNode).mrmlSliceNode()
                sliceNode.SetSliceToRASByNTP(n[0], n[1], n[2], t[0], t[1], t[2], p0[0], p0[1], p0[2], 0)

                sliceToRas = sliceNode.GetSliceToRAS()
                if (sliceToRas.GetElement(1, 0) > 0 and sliceToRas.GetElement(1, 2) > 0) or (sliceToRas.GetElement(0, 2) > 0 and sliceToRas.GetElement(1, 0) < 0):
                    transform = vtk.vtkTransform()
                    transform.SetMatrix(sliceToRas)
                    transform.RotateZ(180)
                    #transform.RotateY(180)
                    sliceToRas.DeepCopy(transform.GetMatrix())
                    sliceNode.UpdateMatrices()

                #rescaling dimensions to zoom in using slice node's aspect ratio
                #print(sliceNode.GetFieldOfView())
                if aspectRatio is not None:
                    x = aspectRatio # lower number = zoom-in default 50, for pano ~10
                    y = x * sliceNode.GetFieldOfView()[1] / sliceNode.GetFieldOfView()[0]
                    z = sliceNode.GetFieldOfView()[2]
                    sliceNode.SetFieldOfView(x,y,z)

                if rotateZ is not None:
                    transform = vtk.vtkTransform()
                    transform.SetMatrix(sliceToRas)
                    transform.RotateY(rotateZ)
                    sliceToRas.DeepCopy(transform.GetMatrix())
                    sliceNode.UpdateMatrices()

                if rotateT is not None:
                    transform = vtk.vtkTransform()
                    transform.SetMatrix(sliceToRas)
                    transform.RotateX(rotateT)
                    sliceToRas.DeepCopy(transform.GetMatrix())
                    sliceNode.UpdateMatrices()
                
                sliceNode.Modified()
                
                transform = slicer.util.getNode(planeNode.GetName()+'_trans')
                #transform.SetMatrix(sliceNode.GetSliceToRAS())
                transform.SetAndObserveMatrixTransformToParent(sliceNode.GetSliceToRAS())
                #sliceNode.GetSliceToRAS().DeepCopy(transform.GetMatrix())
                planeNode.SetAndObserveTransformNodeID(transform.GetID())

                widget = slicer.app.layoutManager().sliceWidget(viewNode)
                view = widget.sliceView()
                view.forceRender()
            
            def transverseStep(self, f):
                if self.curvePoints is not None:
                    self.f = int(f)
                    try:
                        self.reslice_on_path(np.asarray(self.curvePoints.GetPoint(self.f)), np.asarray(self.curvePoints.GetPoint(self.f+1)), "view_transverse_slice", slicer.util.getNode(self.plane_model + "_transverse_slice"), int(self.get_slice_img_dims("view_transverse_slice")[0]/10))
                        #self.widgetClass.update_scene_blender(slicer.util.getNode(self.plane_model + "_transverse_slice"), self.widgetClass.sock, "ViewLink")
                        self.widgetClass.slice_view_numpy("view_transverse_slice", self.plane_model + "_transverse_slice", self.sliceSock, mode="UPDATE")
                        time.sleep(0.5)
                    except slicer.util.MRMLNodeNotFoundException: print("node not found")
                else:
                    #slicer.util.confirmOkCancelDisplay("Open curve path not selected!", "slicerPano Info:")
                    pass

            def tangentialAngle(self, angle):
                if self.curvePoints is not None:
                    try:
                        self.reslice_on_path(np.asarray(self.curvePoints.GetPoint(self.f)), np.asarray(self.curvePoints.GetPoint(self.f+1)), "view_tangential_slice", slicer.util.getNode(self.plane_model + "_tangential_slice"), int(self.get_slice_img_dims("view_tangential_slice")[0]/10), angle)
                        #self.widgetClass.update_scene_blender(slicer.util.getNode(self.plane_model + "_tangential_slice"), self.widgetClass.sock, "ViewLink")
                        self.widgetClass.slice_view_numpy("view_tangential_slice", self.plane_model + "_tangential_slice", self.sliceSock, mode="UPDATE")
                        time.sleep(0.5)
                    except slicer.util.MRMLNodeNotFoundException: print("node not found")
                else:
                    #slicer.util.confirmOkCancelDisplay("Open curve path not selected!", "slicerPano Info:")
                    pass

            def freeViewAngles(self, event_val):
                if self.curvePoints is not None:
                    try:
                        self.reslice_on_path(np.asarray(self.curvePoints.GetPoint(self.f)), np.asarray(self.curvePoints.GetPoint(self.f+1)), "view_freeview_slice", slicer.util.getNode(self.plane_model + "_freeview_slice"), int(self.get_slice_img_dims("view_freeview_slice")[0]/10), self.fv_tan_slider.value, self.fv_ax_slider.value)
                        #self.widgetClass.update_scene_blender(slicer.util.getNode(self.plane_model + "_freeview_slice"), self.widgetClass.sock, "ViewLink")
                        self.widgetClass.slice_view_numpy("view_freeview_slice", self.plane_model + "_freeview_slice", self.sliceSock, mode="UPDATE")
                        time.sleep(0.5)
                    except slicer.util.MRMLNodeNotFoundException: print("node not found")
                else:
                    #slicer.util.confirmOkCancelDisplay("Open curve path not selected!", "slicerPano Info:")
                    pass
                
                #self.model.reslice_on_path(np.asarray(self.curvePoints.GetPoint(self.f)), np.asarray(self.curvePoints.GetPoint(self.f+1)), "Green", None, self.fv_tan_slider.value, self.fv_ax_slider.value)
            '''
            def sliceNodeTransform(self, node, event):
                if node.GetName() == "FreeView" and self.slider_event == False: # TODO: rename slice widgets in layout to respective view they show instead of Green, yellow, red, etc.
                    self.widgetClass.update_scene_blender(slicer.util.getNode(self.plane_model + "_freeview_slice"), self.widgetClass.sock)
                    self.widgetClass.slice_view_numpy("FreeView", self.plane_model + "_freeview_slice", self.sliceSock, mode="UPDATE")
                elif node.GetName() == "Yellow" and self.slider_event == False:
                    self.widgetClass.update_scene_blender(slicer.util.getNode(self.plane_model + "_tangential_slice"), self.widgetClass.sock)
                    self.widgetClass.slice_view_numpy("Yellow", self.plane_model + "_tangential_slice", self.sliceSock, mode="UPDATE")
                elif node.GetName() == "Green" and self.slider_event == False:
                    print("green node event!")
                    self.widgetClass.update_scene_blender(slicer.util.getNode(self.plane_model + "_transverse_slice"), self.widgetClass.sock)
                    self.widgetClass.slice_view_numpy("Green", self.plane_model + "_transverse_slice", self.sliceSock, mode="UPDATE")
            '''
            """
            def view_node(self, node):
                #print(node)
                if node is not None:
                    self.selectedView = node
                    #self.plane_model = node.GetName()
                    self.widgetClass.slice_view_numpy(self.selectedView.GetName(), self.plane_model, self.sliceSock, mode="NEW")

            """

            def curve_node(self, node):
                if node is not None:
                    self.curveNode = node
                    self.curvePoints = node.GetCurvePointsWorld()
                    self.frameSlider.maximum = self.curvePoints.GetNumberOfPoints()-2
                    self.curveNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNodeRed','vtkMRMLSliceNodeview_transverse_slice', 'vtkMRMLSliceNodeview_tangential_slice', "vtkMRMLSliceNodeview_freeview_slice"))
                    for i in range(1,2):
                        self.transverseStep(i)
                    for i in range(1,2):
                        self.tangentialAngle(i)
                    
                    for i in range(1,2):
                        self.fv_tan_slider.value = i
                        self.freeViewAngles(i)
                    '''
                    for i in range(1,2):
                        self.fv_ax_slider.value = i
                        self.freeViewAngles(i)
                    '''
                else: pass

            def onPantomographButtonToggled(self):
                # clear volumes
                try: slicer.mrmlScene.RemoveNode(slicer.util.getNode("straightening_transform"))
                except slicer.util.MRMLNodeNotFoundException: pass
                try: slicer.mrmlScene.RemoveNode(slicer.util.getNode("straight_volume"))
                except slicer.util.MRMLNodeNotFoundException: pass

                straighteningTransformNode = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLTransformNode', 'straightening_transform')
                straightenedVolumeNode = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode', 'straight_volume')
                self.widgetClass.computeStraighteningTransform(straighteningTransformNode, self.curveNode, [float(self.pano_y.text), float(self.pano_x.text)], float(self.curve_res.text))
                self.widgetClass.straightenVolume(straightenedVolumeNode,  self.widgetClass.workingVolume, [float(self.slice_res.text), float(self.slice_res.text), float(self.curve_res.text)], straighteningTransformNode)

                all_models = [slicer.mrmlScene.GetNthNodeByClass(h, "vtkMRMLModelNode") for h in range(slicer.mrmlScene.GetNumberOfNodesByClass("vtkMRMLModelNode"))]
                straightned_models = []
                working_models = []

                #nodeToClone = slicer.util.getNode("lower IO")
                #try: slicer.mrmlScene.RemoveNode(slicer.util.getNode("lower IO_straightened"))
                #except slicer.util.MRMLNodeNotFoundException: pass
                # Clone the node
                '''
                shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
                itemIDToClone = shNode.GetItemByDataNode(nodeToClone)
                clonedItemID = slicer.modules.subjecthierarchy.logic().CloneSubjectHierarchyItem(shNode, itemIDToClone)
                clonedNode = shNode.GetItemDataNode(clonedItemID)
                clonedNode.SetName(nodeToClone.GetName()+"_straightened")
                clonedNode.GetDisplayNode().SetSliceIntersectionVisibility(True)
                clonedNode.GetDisplayNode().SetSliceIntersectionThickness(2)
                '''
                for model in all_models:
                    if "Slice" not in model.GetName():
                        if "slice" not in model.GetName():
                            if "_straightened" in model.GetName():
                                straightned_models.append(model)
                            else: working_models.append(model)

                #print([t.GetName() for t in straightned_models])
                #print([t.GetName() for t in working_models])

                for m_straight in straightned_models:
                    slicer.mrmlScene.RemoveNode(m_straight)
                    slicer.mrmlScene.RemoveNode(slicer.util.getNode(m_straight.GetName()[:-len("_straightened")]+"_pano_trans"))

                for model in working_models:
                    #clone model
                    shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
                    itemIDToClone = shNode.GetItemByDataNode(model)
                    clonedItemID = slicer.modules.subjecthierarchy.logic().CloneSubjectHierarchyItem(shNode, itemIDToClone)
                    clonedNode = shNode.GetItemDataNode(clonedItemID)
                    clonedNode.SetName(model.GetName()+"_straightened")
                    clonedNode.GetDisplayNode().SetSliceIntersectionVisibility(True)
                    clonedNode.GetDisplayNode().SetSliceIntersectionThickness(2)
                    #clone straightening transform
                    #shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
                    itemIDToClone = shNode.GetItemByDataNode(slicer.util.getNode(model.GetName()+"_trans"))
                    clonedItemID = slicer.modules.subjecthierarchy.logic().CloneSubjectHierarchyItem(shNode, itemIDToClone)
                    clonedBlenderTrans = shNode.GetItemDataNode(clonedItemID)
                    clonedBlenderTrans.SetName(model.GetName() + "_pano_trans")
                    
                    clonedBlenderTrans.SetAndObserveTransformNodeID(slicer.util.getNode("straightening_transform").GetID())
                    #slicer.vtkSlicerTransformLogic().hardenTransform(clonedNode)
                    
                    clonedNode.SetAndObserveTransformNodeID(clonedBlenderTrans.GetID())
                    clonedNode.GetDisplayNode().SetViewNodeIDs(('vtkMRMLSliceNode2D',)) #('vtkMRMLViewNode1', 'vtkMRMLSliceNodeRed', 'vtkMRMLSliceNodeGreen', 'vtkMRMLSliceNodeYellow')
                #clonedNode.GetDisplayNode().Modified()

                slicer.util.setSliceViewerLayers(background=self.widgetClass.workingVolume)
                straight_viewNode = slicer.app.layoutManager().sliceWidget("2D").sliceLogic()
                straight_viewNode.GetSliceCompositeNode().SetBackgroundVolumeID(slicer.util.getNode('straight_volume').GetID())

            def rotate_normal(self, angle):
                sliceNode = slicer.app.layoutManager().sliceWidget("2D").mrmlSliceNode()
                sliceToRas = sliceNode.GetSliceToRAS()
                transform = vtk.vtkTransform()
                transform.SetMatrix(sliceToRas)
                #transform.RotateZ(90)
                transform.RotateX(1)
                sliceToRas.DeepCopy(transform.GetMatrix())
                sliceNode.UpdateMatrices()
                sliceNode.Modified()
        
        self.sliceViewCache[name] = sliceViewPanel(name, self, self.layout, self.parent)
        #print(self.sliceViewCache)

    def delete_slice_view(self, name):
        self.sliceViewCache[name].sliceSock.handle_close()
        #self.sliceViewCache[name].name_disp.deleteLater()
        #self.sliceViewCache[name].sliceNodeSelector.deleteLater()
        self.sliceViewCache[name].inputFiducialsNodeSelector.deleteLater()
        self.sliceViewCache[name].frameSlider.deleteLater()
        self.sliceViewCache[name].rotateView.deleteLater()
        self.sliceViewCache[name].fv_tan_slider.deleteLater()
        self.sliceViewCache[name].fv_ax_slider.deleteLater()
        if self.slicer_3dview == False:
            self.sliceViewCache[name].curve_res.deleteLater()
            self.sliceViewCache[name].slice_res.deleteLater()
            self.sliceViewCache[name].pano_x.deleteLater()
            self.sliceViewCache[name].pano_y.deleteLater()
            self.sliceViewCache[name].PantomographButton.deleteLater()
            self.sliceViewCache[name].normal_angle.deleteLater()
        self.sliceViewCache[name].sliceViewLayout.deleteLater()
        self.sliceViewCache[name].sliceViewSettings.deleteLater()
        self.sliceViewCache[name].freeviewCollapsibleButton.deleteLater()
        del self.sliceViewCache[name]
        #self.layout.update()

    def slice_view_numpy(self, sliceNodeID, modelName, socket, mode="NEW"):
        sliceNodeID = 'vtkMRMLSliceNode%s'%sliceNodeID
        transparentBackground = True
        
        # Get image data from slice view
        sliceNode = slicer.mrmlScene.GetNodeByID(sliceNodeID)
        viewNodeID = sliceNodeID

        cap = ScreenCapture.ScreenCaptureLogic()
        view = cap.viewFromNode(slicer.mrmlScene.GetNodeByID(viewNodeID))
        # Capture single view

        rw = view.renderWindow()
        wti = vtk.vtkWindowToImageFilter()

        if transparentBackground:
            originalAlphaBitPlanes = rw.GetAlphaBitPlanes()
            rw.SetAlphaBitPlanes(1)
            ren=rw.GetRenderers().GetFirstRenderer()
            originalGradientBackground = ren.GetGradientBackground()
            ren.SetGradientBackground(False)
            wti.SetInputBufferTypeToRGBA()
            rw.Render() # need to render after changing bit planes

        wti.SetInput(rw)
        wti.Update()

        if transparentBackground:
            rw.SetAlphaBitPlanes(originalAlphaBitPlanes)
            ren.SetGradientBackground(originalGradientBackground)

        capturedImage = wti.GetOutput()

        imageSize = capturedImage.GetDimensions()

        if imageSize[0]<2 or imageSize[1]<2:
            # image is too small, most likely it is invalid
            raise ValueError('Capture image from view failed')

        #scale down the image for quicker copy
        resample = vtk.vtkImageResample()
        resample.SetInputData(capturedImage)
        resample.SetAxisMagnificationFactor(0, 0.35)
        resample.SetAxisMagnificationFactor(1, 0.35)
        resample.Update()
        capturedImage = resample.GetOutput()
        # Make sure image witdth and height is even, otherwise encoding may fail
        imageWidthOdd = (imageSize[0] & 1 == 1)
        imageHeightOdd = (imageSize[1] & 1 == 1)
        if imageWidthOdd or imageHeightOdd:
            imageClipper = vtk.vtkImageClip()
            imageClipper.SetClipData(True)
            imageClipper.SetInputData(capturedImage)
            extent = capturedImage.GetExtent()
            imageClipper.SetOutputWholeExtent(extent[0], extent[1]-1 if imageWidthOdd else extent[1],
                                                extent[2], extent[3]-1 if imageHeightOdd else extent[3],
                                                extent[4], extent[5])
            imageClipper.Update()
            capturedImage = imageClipper.GetOutput()
        
        sc = capturedImage.GetPointData().GetScalars()
        a = vtk_to_numpy(sc)
        a = a.flatten().tolist()
        #print(capturedImage)
        #print(capturedImage.GetDimensions())
        #print(imageSize)
        #print(modelName)
        #image_w = capturedImage.GetDimensions()[0]
        #image_h = capturedImage.GetDimensions()[1]
        if self.sliceViewCache["view_obj"].slice_dims_buff[modelName] is None:
            mode = "NEW"
            self.sliceViewCache["view_obj"].slice_dims_buff[modelName] = imageSize
        elif self.sliceViewCache["view_obj"].slice_dims_buff[modelName] is not None and self.sliceViewCache["view_obj"].slice_dims_buff[modelName] != imageSize:
            mode = "NEW"
            self.sliceViewCache["view_obj"].slice_dims_buff[modelName] = imageSize
        elif self.sliceViewCache["view_obj"].slice_dims_buff[modelName] is not None and self.sliceViewCache["view_obj"].slice_dims_buff[modelName] == imageSize:
            mode = "UPDATE"
        #print(mode)
        if mode == "NEW":
            socket.send_data("SLICE_UPDATE", sliceNode.GetName() + "_BREAK_" + modelName + "_BREAK_" + str(capturedImage.GetDimensions()) + "_BREAK_" + str(imageSize) + "_BREAK_" + str(a))
        if mode == "UPDATE":
            socket.send_data("SLICE_UPDATE", sliceNode.GetName() + "_BREAK_" + modelName + "_BREAK_" + str(capturedImage.GetDimensions()) + "_BREAK_" + str(imageSize) + "_BREAK_" + str(a))
            self.update_scene_blender(slicer.util.getNode(modelName), socket, "ViewLink")

    def slice_view_update_scene(self,  xml):
        self.update_scene(xml)
        try: #any better ideas??
            tree = ET.ElementTree(ET.fromstring(xml))
        except:
            return
        x_scene = tree.getroot()
        
        s_scene = slicer.mrmlScene
        #scene = slicer.mrmlScene
        for b_ob in x_scene:
            #get the name of blender object
            name = b_ob.get('name')
            slice_name = b_ob.get('name').replace("_obj", '')
            slice_widget_node = slicer.app.layoutManager().sliceWidget(slice_name).mrmlSliceNode()
            
            sliceToRas = slice_widget_node.GetSliceToRAS()
            transform_matrix = vtk.vtkMatrix4x4()
            slicer.vtkMRMLTransformNode().GetMatrixTransformFromNode(slicer.util.getNode(name+'_trans'), transform_matrix)
            sliceToRas.DeepCopy(transform_matrix)
            slice_widget_node.UpdateMatrices()
            slice_widget_node.Modified()

            self.slice_view_numpy(name.replace("_obj", ''), name, self.sock, mode="UPDATE")

    def save_project(self, abspath):
        abspath += ".mrb"
        if slicer.util.saveScene(abspath):
            print("Scene saved to: {0}".format(abspath))
        else:
            print("Scene saving failed")

    #https://github.com/PerkLab/SlicerSandbox/blob/master/CurvedPlanarReformat/CurvedPlanarReformat.py
    def computeStraighteningTransform(self, transformToStraightenedNode, curveNode, sliceSizeMm, outputSpacingMm):
        """
        Compute straightened volume (useful for example for visualization of curved vessels)
        resamplingCurveSpacingFactor: 
        """
        self.transformSpacingFactor = 5.0
        # Create a temporary resampled curve
        resamplingCurveSpacing = outputSpacingMm * self.transformSpacingFactor
        originalCurvePoints = curveNode.GetCurvePointsWorld()
        sampledPoints = vtk.vtkPoints()
        if not slicer.vtkMRMLMarkupsCurveNode.ResamplePoints(originalCurvePoints, sampledPoints, resamplingCurveSpacing, False):
            raise("Redampling curve failed")
        resampledCurveNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsCurveNode", "CurvedPlanarReformat_resampled_curve_temp")
        resampledCurveNode.SetNumberOfPointsPerInterpolatingSegment(1)
        resampledCurveNode.SetCurveTypeToLinear()
        resampledCurveNode.SetControlPointPositionsWorld(sampledPoints)
        numberOfSlices = resampledCurveNode.GetNumberOfControlPoints()

        # Z axis (from first curve point to last, this will be the straightened curve long axis)
        curveStartPoint = np.zeros(3)
        curveEndPoint = np.zeros(3)
        resampledCurveNode.GetNthControlPointPositionWorld(0, curveStartPoint)
        resampledCurveNode.GetNthControlPointPositionWorld(resampledCurveNode.GetNumberOfControlPoints()-1, curveEndPoint)
        transformGridAxisZ = (curveEndPoint-curveStartPoint)/np.linalg.norm(curveEndPoint-curveStartPoint)
    
        # X axis = average X axis of curve, to minimize torsion (and so have a simple displacement field, which can be robustly inverted)
        sumCurveAxisX_RAS = np.zeros(3)
        for gridK in range(numberOfSlices):
            curvePointToWorld = vtk.vtkMatrix4x4()
            resampledCurveNode.GetCurvePointToWorldTransformAtPointIndex(resampledCurveNode.GetCurvePointIndexFromControlPointIndex(gridK), curvePointToWorld)
            curvePointToWorldArray = slicer.util.arrayFromVTKMatrix(curvePointToWorld)
            curveAxisX_RAS = curvePointToWorldArray[0:3, 0]
            sumCurveAxisX_RAS += curveAxisX_RAS
        meanCurveAxisX_RAS = sumCurveAxisX_RAS/np.linalg.norm(sumCurveAxisX_RAS)
        transformGridAxisX = meanCurveAxisX_RAS

        # Y axis
        transformGridAxisY = np.cross(transformGridAxisZ, transformGridAxisX)
        transformGridAxisY = transformGridAxisY/np.linalg.norm(transformGridAxisY)

        # Make sure that X axis is orthogonal to Y and Z
        transformGridAxisX = np.cross(transformGridAxisY, transformGridAxisZ)
        transformGridAxisX = transformGridAxisX/np.linalg.norm(transformGridAxisX)

        # Origin (makes the grid centered at the curve)
        curveLength = resampledCurveNode.GetCurveLengthWorld()
        curveNodePlane = vtk.vtkPlane()
        slicer.modules.markups.logic().GetBestFitPlane(resampledCurveNode, curveNodePlane)
        transformGridOrigin = np.array(curveNodePlane.GetOrigin())
        transformGridOrigin -= transformGridAxisX * sliceSizeMm[0]/2.0
        transformGridOrigin -= transformGridAxisY * sliceSizeMm[1]/2.0
        transformGridOrigin -= transformGridAxisZ * curveLength/2.0

        # Create grid transform
        # Each corner of each slice is mapped from the original volume's reformatted slice
        # to the straightened volume slice.
        # The grid transform contains one vector at the corner of each slice.
        # The transform is in the same space and orientation as the straightened volume.

        gridDimensions = [2, 2, numberOfSlices]
        gridSpacing = [sliceSizeMm[0], sliceSizeMm[1], resamplingCurveSpacing]
        gridDirectionMatrixArray = np.eye(4)
        gridDirectionMatrixArray[0:3, 0] = transformGridAxisX
        gridDirectionMatrixArray[0:3, 1] = transformGridAxisY
        gridDirectionMatrixArray[0:3, 2] = transformGridAxisZ
        gridDirectionMatrix = slicer.util.vtkMatrixFromArray(gridDirectionMatrixArray)

        gridImage = vtk.vtkImageData()
        gridImage.SetOrigin(transformGridOrigin)
        gridImage.SetDimensions(gridDimensions)
        gridImage.SetSpacing(gridSpacing)
        gridImage.AllocateScalars(vtk.VTK_DOUBLE, 3)
        transform = slicer.vtkOrientedGridTransform()
        transform.SetDisplacementGridData(gridImage)
        transform.SetGridDirectionMatrix(gridDirectionMatrix)
        transformToStraightenedNode.SetAndObserveTransformFromParent(transform)

        # Compute displacements
        transformDisplacements_RAS = slicer.util.arrayFromGridTransform(transformToStraightenedNode)
        for gridK in range(gridDimensions[2]):
            curvePointToWorld = vtk.vtkMatrix4x4()
            resampledCurveNode.GetCurvePointToWorldTransformAtPointIndex(resampledCurveNode.GetCurvePointIndexFromControlPointIndex(gridK), curvePointToWorld)
            curvePointToWorldArray = slicer.util.arrayFromVTKMatrix(curvePointToWorld)
            curveAxisX_RAS = curvePointToWorldArray[0:3, 0]
            curveAxisY_RAS = curvePointToWorldArray[0:3, 1]
            curvePoint_RAS = curvePointToWorldArray[0:3, 3]
            for gridJ in range(gridDimensions[1]):
                for gridI in range(gridDimensions[0]):
                    straightenedVolume_RAS = (transformGridOrigin
                        + gridI*gridSpacing[0]*transformGridAxisX
                        + gridJ*gridSpacing[1]*transformGridAxisY
                        + gridK*gridSpacing[2]*transformGridAxisZ)
                    inputVolume_RAS = (curvePoint_RAS
                        + (gridI-0.5)*sliceSizeMm[0]*curveAxisX_RAS
                        + (gridJ-0.5)*sliceSizeMm[1]*curveAxisY_RAS)
                    transformDisplacements_RAS[gridK][gridJ][gridI] = inputVolume_RAS - straightenedVolume_RAS
        slicer.util.arrayFromGridTransformModified(transformToStraightenedNode)

        slicer.mrmlScene.RemoveNode(resampledCurveNode)  # delete temporary curve

    def straightenVolume(self, outputStraightenedVolume, volumeNode, outputStraightenedVolumeSpacing, straighteningTransformNode):
        """
        Compute straightened volume (useful for example for visualization of curved vessels)
        """
        gridTransform = straighteningTransformNode.GetTransformFromParentAs("vtkOrientedGridTransform")
        if not gridTransform:
            raise ValueError("Straightening transform is expected to contain a vtkOrientedGridTransform form parent")

        # Get transformation grid geometry
        gridIjkToRasDirectionMatrix = gridTransform.GetGridDirectionMatrix()
        gridTransformImage = gridTransform.GetDisplacementGrid()
        gridOrigin = gridTransformImage.GetOrigin()
        gridSpacing = gridTransformImage.GetSpacing()
        gridDimensions = gridTransformImage.GetDimensions()
        gridExtentMm = [gridSpacing[0]*(gridDimensions[0]-1), gridSpacing[1]*(gridDimensions[1]-1), gridSpacing[2]*(gridDimensions[2]-1)]

        # Compute IJK to RAS matrix of output volume
        # Get grid axis directions
        straightenedVolumeIJKToRASArray = slicer.util.arrayFromVTKMatrix(gridIjkToRasDirectionMatrix)
        # Apply scaling
        straightenedVolumeIJKToRASArray = np.dot(straightenedVolumeIJKToRASArray, np.diag([outputStraightenedVolumeSpacing[0], outputStraightenedVolumeSpacing[1], outputStraightenedVolumeSpacing[2], 1]))
        # Set origin
        straightenedVolumeIJKToRASArray[0:3,3] = gridOrigin 

        outputStraightenedImageData = vtk.vtkImageData()
        outputStraightenedImageData.SetExtent(0, int(gridExtentMm[0]/outputStraightenedVolumeSpacing[0])-1, 0, int(gridExtentMm[1]/outputStraightenedVolumeSpacing[1])-1, 0, int(gridExtentMm[2]/outputStraightenedVolumeSpacing[2])-1)
        outputStraightenedImageData.AllocateScalars(volumeNode.GetImageData().GetScalarType(), volumeNode.GetImageData().GetNumberOfScalarComponents())
        outputStraightenedVolume.SetAndObserveImageData(outputStraightenedImageData)
        outputStraightenedVolume.SetIJKToRASMatrix(slicer.util.vtkMatrixFromArray(straightenedVolumeIJKToRASArray))

        # Resample input volume to straightened volume
        parameters = {}
        parameters["inputVolume"] = volumeNode.GetID()
        parameters["outputVolume"] = outputStraightenedVolume.GetID()
        parameters["referenceVolume"] = outputStraightenedVolume.GetID()
        parameters["transformationFile"] = straighteningTransformNode.GetID()
        resamplerModule = slicer.modules.resamplescalarvectordwivolume
        parameterNode = slicer.cli.runSync(resamplerModule, None, parameters)

        outputStraightenedVolume.CreateDefaultDisplayNodes()
        outputStraightenedVolume.GetDisplayNode().CopyContent(volumeNode.GetDisplayNode())
        slicer.mrmlScene.RemoveNode(parameterNode)

    '''
    def onbtn_select_volumeClicked(self, volumeNode):
        if volumeNode is not None:
            slicer.util.setSliceViewerLayers(background=volumeNode)
            self.workingVolume = volumeNode
        else:
            #slicer.util.confirmOkCancelDisplay("Volume not selected!", "slicerPano Info:")
            pass

    def onPlayButtonToggled(self, checked):
        if checked:
            self.watching = True
            self.playButton.text = "Stop"
            if self.sock == None:
                self.sock = asyncsock.SlicerComm.EchoClient(str(self.host_address.text), int(self.host_port.text), [("XML", self.update_scene), ("OBJ", self.import_obj_from_blender), ("OBJ_MULTIPLE", self.import_multiple), ("CHECK", self.obj_check_handle), ("DEL", self.delete_model), ("SETUP_SLICE", self.add_slice_view), ("DEL_SLICE", self.delete_slice_view), ("FILE_OBJ", self.FILE_import_obj_from_blender), ("FILE_OBJ_MULTIPLE", self.FILE_import_multiple), ("CONFIG_PARAMS", self.blender_config_params), ("VIEW_UPDATE", self.slice_view_update_scene)], self.log_debug.isChecked())
                #self.sock.send_data("TEST", 'bogus data from slicer!')
        else:
            self.watching = False
            self.playButton.text = "Start"
            self.sock.handle_close()
            self.sock = None
    '''
                
    def frameDelaySliderValueChanged(self, newValue):
        #print "frameDelaySliderValueChanged:", newValue
        self.timer.interval = newValue


