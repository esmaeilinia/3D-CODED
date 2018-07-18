from __future__ import print_function
import numpy as np
import torch.utils.data
from torch.autograd import Variable
import sys
sys.path.append('./auxiliary/')
from model import *
from utils import *
from ply import *
import sys
sys.path.append("./nndistance/")
from modules.nnd import NNDModule
distChamfer = NNDModule()
import global_variables

val_loss = AverageValueMeter()



def regress(points):
    """
    search the latent space to global_variables. Optimize reconstruction using the Chamfer Distance
    :param points: input points to reconstruct
    :return pointsReconstructed: final reconstruction after optimisation
    """
    points = Variable(points.data, requires_grad=True)
    latent_code = global_variables.network.encoder(points)
    lrate = 0.001  # learning rate
    # define parameters to be optimised and optimiser
    input_param = nn.Parameter(latent_code.data, requires_grad=True)
    global_variables.optimizer = global_variables.optim.Adam([input_param], lr=lrate)
    loss = 10
    i = 0

    #learning loop
    while np.log(loss) > -9 and i < global_variables.opt.nepoch:
        global_variables.optimizer.zero_grad()
        pointsReconstructed = global_variables.network.decode(input_param)  # forward pass
        dist1, dist2 = distChamfer(points.transpose(2, 1).contiguous(), pointsReconstructed)
        loss_net = (torch.mean(dist1)) + (torch.mean(dist2))
        loss_net.backward()
        global_variables.optimizer.step()
        loss = loss_net.data[0]
        i = i + 1
    with torch.no_grad():
        if global_variables.opt.HR:
            pointsReconstructed = global_variables.network.decode_full(input_param)  # forward pass
        else :
            pointsReconstructed = global_variables.network.decode(input_param)  # forward pass
    # print("loss reg : ", loss)
    return pointsReconstructed

def run(input):
    """
    :param input: input mesh to reconstruct optimally.
    :return: final reconstruction after optimisation
    """
    if not global_variables.opt.HR:
        mesh_ref = global_variables.mesh_ref_LR
    else:
        mesh_ref = global_variables.mesh_ref

    ## Extract points and put them on GPU
    points = input.vertices
    random_sample = np.random.choice(np.shape(points)[0], size=10000)
    points = torch.from_numpy(points.astype(np.float32)).contiguous().unsqueeze(0)
    points = Variable(points)
    points = points.transpose(2, 1).contiguous()
    points = points.cuda()

    # Get a low resolution PC to find the best reconstruction after a rotation on the Y axis
    points_LR = torch.from_numpy(input.vertices[random_sample].astype(np.float32)).contiguous().unsqueeze(0)
    points_LR = Variable(points_LR)
    points_LR = points_LR.transpose(2, 1).contiguous()
    points_LR = points_LR.cuda()

    theta = 0
    bestLoss = 10
    # print("size: ", points_LR.size())
    pointsReconstructed = global_variables.network(points_LR)
    dist1, dist2 = distChamfer(points_LR.transpose(2, 1).contiguous(), pointsReconstructed)
    loss_net = (torch.mean(dist1)) + (torch.mean(dist2))
    # print("loss : ",  loss_net.data[0], 0)

    # ---- Search best angle for best reconstruction on the Y axis---
    for theta in np.linspace(-np.pi/2, np.pi/2, 100):
        #  Rotate mesh by theta and renormalise
        rot_matrix = np.array([[np.cos(theta), 0, np.sin(theta)], [0, 1, 0], [- np.sin(theta), 0,  np.cos(theta)]])
        rot_matrix = Variable(torch.from_numpy(rot_matrix).float()).cuda()
        points2 = torch.matmul(rot_matrix, points_LR)
        mesh_tmp = pymesh.form_mesh(vertices=points2[0].transpose(1,0).data.cpu().numpy(), faces=global_variables.network.mesh.faces)
        norma = Variable(torch.from_numpy((mesh_tmp.bbox[0] + mesh_tmp.bbox[1]) / 2).float().cuda())
        norma2 = norma.unsqueeze(1).expand(3,points2.size(2)).contiguous()
        points2[0] = points2[0] - norma2
        mesh_tmp = pymesh.form_mesh(vertices=points2[0].transpose(1,0).data.cpu().numpy(), faces=np.array([[0,0,0]]))

        # reconstruct rotated mesh
        pointsReconstructed = global_variables.network(points2)
        dist1, dist2 = distChamfer(points2.transpose(2, 1).contiguous(), pointsReconstructed)


        loss_net = (torch.mean(dist1)) + (torch.mean(dist2))
        if loss_net < bestLoss:
            bestLoss = loss_net
            best_theta = theta
            # unrotate the mesh
            norma3 = norma.unsqueeze(0).expand(pointsReconstructed.size(1), 3).contiguous()
            pointsReconstructed[0] = pointsReconstructed[0] + norma3
            rot_matrix = np.array([[np.cos(-theta), 0, np.sin(-theta)], [0, 1, 0], [- np.sin(-theta), 0,  np.cos(-theta)]])
            rot_matrix = Variable(torch.from_numpy(rot_matrix).float()).cuda()
            pointsReconstructed = torch.matmul(pointsReconstructed, rot_matrix.transpose(1,0))
            bestPoints = pointsReconstructed

    # print("best loss and angle : ", bestLoss.data[0], best_theta)
    val_loss.update(bestLoss.data[0])

    if global_variables.opt.HR:
        faces_tosave = global_variables.network.mesh_HR.faces
    else:
        faces_tosave = global_variables.network.mesh.faces
    
    # create initial guess
    mesh = pymesh.form_mesh(vertices=bestPoints[0].data.cpu().numpy(), faces=global_variables.network.mesh.faces)
    mesh.add_attribute("red")
    mesh.add_attribute("green")
    mesh.add_attribute("blue")
    mesh.set_attribute("red", global_variables.mesh_ref_LR.get_attribute("vertex_red"))
    mesh.set_attribute("green", global_variables.mesh_ref_LR.get_attribute("vertex_green"))
    mesh.set_attribute("blue", global_variables.mesh_ref_LR.get_attribute("vertex_blue"))

    #START REGRESSION
    print("start regression...")
    
    # rotate with optimal angle
    rot_matrix = np.array([[np.cos(best_theta), 0, np.sin(best_theta)], [0, 1, 0], [- np.sin(best_theta), 0,  np.cos(best_theta)]])
    rot_matrix = Variable(torch.from_numpy(rot_matrix).float()).cuda()
    points2 = torch.matmul(rot_matrix, points)
    mesh_tmp = pymesh.form_mesh(vertices=points2[0].transpose(1,0).data.cpu().numpy(), faces=global_variables.network.mesh.faces)
    norma = Variable(torch.from_numpy((mesh_tmp.bbox[0] + mesh_tmp.bbox[1]) / 2).float().cuda())
    norma2 = norma.unsqueeze(1).expand(3,points2.size(2)).contiguous()
    points2[0] = points2[0] - norma2
    pointsReconstructed1 = regress(points2)
    # unrotate with optimal angle
    norma3 = norma.unsqueeze(0).expand(pointsReconstructed1.size(1), 3).contiguous()
    rot_matrix = np.array([[np.cos(-best_theta), 0, np.sin(-best_theta)], [0, 1, 0], [- np.sin(-best_theta), 0,  np.cos(-best_theta)]])
    rot_matrix = Variable(torch.from_numpy(rot_matrix).float()).cuda()
    pointsReconstructed1[0] = pointsReconstructed1[0] + norma3
    pointsReconstructed1 = torch.matmul(pointsReconstructed1, rot_matrix.transpose(1,0))
    
    # create optimal reconstruction
    meshReg = pymesh.form_mesh(vertices=pointsReconstructed1[0].data.cpu().numpy(), faces=faces_tosave)
    meshReg.add_attribute("red")
    meshReg.add_attribute("green")
    meshReg.add_attribute("blue")
    meshReg.set_attribute("red", mesh_ref.get_attribute("vertex_red"))
    meshReg.set_attribute("green", mesh_ref.get_attribute("vertex_green"))
    meshReg.set_attribute("blue", mesh_ref.get_attribute("vertex_blue"))
    return mesh, meshReg


def reconstruct(input_p):
    """
    Recontruct a 3D shape by deforming a template
    :param input_p: input path
    :return: None (but save reconstruction)
    """
    input = pymesh.load_mesh(input_p)
    if global_variables.opt.clean:
        input = clean(input) #remove points that doesn't belong to any edges
    test_orientation(input)
    mesh, meshReg = run(input)
    pymesh.meshio.save_mesh(input_p[:-4] + "InitialGuess.ply", mesh, "red", "green", "blue", ascii=True)
    pymesh.meshio.save_mesh(input_p[:-4] + "FinalReconstruction.ply", meshReg, "red", "green", "blue", ascii=True)
