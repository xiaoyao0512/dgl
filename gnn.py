#from dataset import GraphDataset
#import matplotlib.pyplot as plt
import networkx as nx
import dgl
import torch
###############################################################################
# The return type of :func:`dgl.batch` is still a graph. In the same way, 
# a batch of tensors is still a tensor. This means that any code that works
# for one graph immediately works for a batch of graphs. More importantly,
# because DGL processes messages on all nodes and edges in parallel, this greatly
# improves efficiency.
#
# Graph classifier
# ----------------
# Graph classification proceeds as follows.
#
# .. image:: https://data.dgl.ai/tutorial/batch/graph_classifier.png
#
# From a batch of graphs, perform message passing and graph convolution
# for nodes to communicate with others. After message passing, compute a
# tensor for graph representation from node (and edge) attributes. This step might 
# be called readout or aggregation. Finally, the graph 
# representations are fed into a classifier :math:`g` to predict the graph labels.
#
# Graph convolution layer can be found in the ``dgl.nn.<backend>`` submodule.

from dgl.nn.pytorch import GraphConv
from dgl.nn.pytorch import GATConv
from dgl.nn.pytorch import GatedGraphConv
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
#from torch.utils.data import DataLoader
#from sklearn.model_selection import KFold as kfold
#import torch as th
from ray.rllib.models.torch.torch_modelv2 import TorchModelV2
from ray.rllib.models.tf.tf_modelv2 import TFModelV2
from ray.rllib.utils.annotations import override
import gym
import logging
import numpy as np
from ray.rllib.utils import try_import_tf
#from ray.rllib.models.tf.misc import normc_initializer, get_activation_fn

tf = try_import_tf()
#logger = logging.getLogger(__name__)



'''
def collate(samples):
    # The input `samples` is a list of pairs
    #  (graph, label).
    graphs, labels = map(list, zip(*samples))
    batched_graph = dgl.batch(graphs)
    return batched_graph, torch.tensor(labels)

def nodeFeatures(g, types):
    #g = dgl.add_self_loop(g)
    #graph = dgl.DGLGraph.to_networkx(g)
    if (types == "simple"):
        return g.in_degrees()
    elif (types == "weight"):
        return dgl.khop_adj(g, 1) #g.ndata['w']
    elif (types == "multifractal"):
        return multifractal.multifractal(g)
'''



class GCNClassifier(TFModelV2, nn.Module):
    def __init__(self, obs_space,
                 action_space, num_outputs: int,
                 model_config, name):
        super(GCNClassifier, self).__init__(
            obs_space, action_space, num_outputs, model_config, name)
        nn.Module.__init__(self)
        #self.mode = mode
        self.in_dim = int(np.product(obs_space.shape))#model_config.get("in_dim")
        custom_config = model_config.get("custom_options")
        #print("custom_config = ", custom_config)
        self.hidden_dim = custom_config.get("hidden_dim")
        self.num_outputs = int(np.product(self.obs_space.shape))
        self.num_layers = custom_config.get("num_layers")
        #print("model_config = ", model_config)
        #print("num_lyers = ", self.num_layers)
        self.conv = []
        self._logits = None
        # Holds the current "base" output (before logits layer).
        self._features = None
        # Holds the last input, in case value branch is separate.
        self._last_flat_in = None
        for i in range(self.num_layers-1):
            if (i == 0):
                self.conv.append(GraphConv(self.in_dim, self.hidden_dim))
            else:
                self.conv.append(GraphConv(self.hidden_dim, self.hidden_dim))
        if (self.num_layers == 1):
            self.conv.append(GraphConv(self.in_dim, self.num_outputs))
        else:
            self.conv.append(GraphConv(self.hidden_dim, self.num_outputs))
        #self.conv1 = GraphConv(in_dim, hidden_dim)
        #self.conv2 = GraphConv(hidden_dim, hidden_dim) # graph attention network / gated GNN
        #self.conv3 = GraphConv(hidden_dim, hidden_dim) # graph attention network / gated GNN
        self.value_branch = nn.Linear(self.num_outputs, 1)


    @override(TFModelV2)
    def forward(self, input_dict,
                state,
                seq_lens):
        # subject to change below
        print("obs = ", input_dict["obs_flat"])
        g = input_dict["obs"].float()
        h = g.ndata['m'].view(-1,1).float()
        g = dgl.add_self_loop(g)
        
        # Perform graph convolution and activation function.
        for conv in self.conv:
            h = F.relu(conv(g, h))
        g.ndata['h'] = h
        # Calculate graph representation by averaging all the node representations.
        logits = dgl.mean_nodes(g, 'h')        
        self._features = logits
        return logits, state

    @override(TFModelV2)
    def value_function(self):
        assert self._features is not None, "must call forward() first"
        return self._value_branch(self._features).squeeze(1)


class GATEDClassifier(nn.Module):
    def __init__(self, in_dim, hidden_dim, n_classes, t=5, e_type=1):
        super(GATEDClassifier, self).__init__()
        self.conv1 = GatedGraphConv(in_dim, hidden_dim, t, e_type)
        # self.conv2 = GatedGraphConv(hidden_dim, hidden_dim, t, e_type) # gated gnn
        # self.conv3 = GatedGraphConv(hidden_dim, hidden_dim, t, e_type) # gated gnn
        self.classify = nn.Linear(hidden_dim, n_classes)

    def forward(self, g):
        # node feature
        # feature = nodeFeatures(g, "multifractal")
        h = g.ndata["m"].view(-1,1).float()
        g = dgl.add_self_loop(g)

        # h = g.in_degrees().view(-1, 1).float()
        # Perform graph convolution and activation function.
        h = F.relu(self.conv1(g, h, th.tensor([0 for i in range(dgl.DGLGraph.number_of_nodes(g))])))
        #h = F.relu(self.conv2(g, h))
        #h = F.relu(self.conv3(g, h))
        g.ndata['h'] = h
        # Calculate graph representation by averaging all the node representations.
        hg = dgl.mean_nodes(g, 'h')
        return self.classify(hg)

class GATClassifier(nn.Module):
    def __init__(self, in_dim, hidden_dim, n_classes, num_heads=1):
        super(GATClassifier, self).__init__()
        self.conv1 = GATConv(in_dim, hidden_dim, num_heads=num_heads)
        self.conv2 = GATConv(hidden_dim, hidden_dim, num_heads=num_heads) # graph attention network
        self.conv3 = GATConv(hidden_dim, hidden_dim, num_heads=num_heads) # graph attention network
        self.classify = nn.Linear(hidden_dim, n_classes)

    def forward(self, g):
        # node feature
        h = g.in_degrees().view(-1, 1).float()
        # Perform graph convolution and activation function.
        h = F.relu(self.conv1(g, h))
        h = F.relu(self.conv2(g, h))
        h = F.relu(self.conv3(g, h))
        g.ndata['h'] = h
        # Calculate graph representation by averaging all the node representations.
        hg = dgl.mean_nodes(g, 'h')
        return self.classify(hg)

def difference(lst1, lst2): 
    return list(set(lst1) - set(lst2)) 


'''


num_instances = len(dataset)
test_ratio = 0.2
test_size = int(num_instances * test_ratio)
train_size = num_instances - test_size


print(num_instances, train_size, test_size)






#trainset = dataset
#data_loader = DataLoader(trainset, batch_size=4, shuffle=True,
#                         collate_fn=collate)


# Create model

kfold = 6
#if (num_instances % kfold != 0):
#    assert False, "Please select a new kfold value."
num_per_fold = int(num_instances / kfold)
batch_size = 64

# num_neurons = [8, 16, 32, 64, 128, 256, 512]
num_neurons = [8]
acc_list = []
acc_list1 = []
#mode = "multifractal"
inp = 1
for num in num_neurons:
    total_acc = 0
    total_acc1 = 0
    # for kf in range(1):
    for kf in range(kfold):
        test_set = range(kf*num_per_fold, (kf+1)*num_per_fold)
        train_set = difference(range(num_instances), test_set)
        print("fold = ", kf)
        print("test_set = ", test_set)
        print("train_set = ", train_set)

        train_data = torch.utils.data.Subset(dataset, train_set)
        test_data = torch.utils.data.Subset(dataset, test_set)

        #if (mode == "multifractal"):
        #    inp = 6
        model = GCNClassifier(1, num, 2)
        #model = GATClassifier(1, num, 2) 
        #model = GATEDClassifier(1, num, 2)
        loss_func = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters(), lr=0.001)
        #train_data, test_data = torch.utils.data.random_split(dataset, (train_size, test_size))
        train_loader = torch.utils.data.DataLoader(train_data, shuffle = True, collate_fn=collate)
        test_loader = torch.utils.data.DataLoader(test_data, shuffle = False, collate_fn=collate)
        
        # Train the model    
        model.train()
        epoch_losses = []
        for epoch in range(300):
            epoch_loss = 0
            print("epoch = ", epoch)
            for iter, (bg, label) in enumerate(train_loader):
                #print(iter, label)
                #prediction = model(bg)
                #bg = dgl.add_self_loop(bg)
                prediction = model(bg)
                #print("pred = ", prediction, ", label = ", label)
                loss = loss_func(prediction, label)
                #loss = loss_func(prediction[0], label)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                epoch_loss += loss.detach().item()
            epoch_loss /= (iter + 1)
            print('Epoch {}, loss {:.4f}'.format(epoch, epoch_loss))
            epoch_losses.append(epoch_loss) 
      
        # Evaluate the testing dataset
        model.eval()
        # Convert a list of tuples to two lists
        test_X, test_Y = map(list, zip(*test_data))
        acc = 0
        acc1 = 0
        for idx in range(len(test_X)):
            x = test_X[idx]
            y = test_Y[idx]
            #print("batch size = ", x)
            #print("#nodes = ", test_bg.batch_num_nodes())
            #test_bg = dgl.add_self_loop(test_bg)
            y = torch.tensor(y).float().view(-1, 1)
            #print('ll = ', ll)
            probs_Y = torch.softmax(model(x), 1)
            #print("In fold ", kf, ', sampled = ', y, ', argmax = ', probs_Y)
            #print("In fold ", kf, ', len = ', len(test_Y))
            #print("label = ", y)
            #probs_Y = probs_Y[0]
            sampled_Y = torch.multinomial(probs_Y, 1)
            argmax_Y = torch.max(probs_Y, 1)[1].view(-1, 1)
            #print("In fold ", kf, ', sampled = ', sampled_Y, ', argmax = ', argmax_Y)
            if (sampled_Y == y):
                acc += 1
            if (argmax_Y == y):
                acc1 += 1
        acc = acc / len(test_Y) * 100
        acc1 = acc1 / len(test_Y) * 100
        print("In fold ", kf, ', Accuracy of sampled predictions on the test set: {:.4f}%'.format(acc))
        print("In fold ", kf, ', Accuracy of sampled predictions on the test set: {:.4f}%'.format(acc1))
        total_acc1 += acc1
        total_acc += acc
        break

    total_acc = total_acc / kfold
    total_acc1 = total_acc1 / kfold
    acc_list.append(total_acc)
    acc_list1.append(total_acc1)
print("Total accuracy cross validation = ", acc_list)
print("Total accuracy cross validation = ", acc_list1)


y_true, y_pred, y_prob  = [], [], []
with torch.no_grad():
  for x, y in test_loader:
    # ground truth
    y = list(y.numpy())
    y_true += y
    
    x = x.float().to(device)
    outputs = model(x)

    # predicted label
    _, predicted = torch.max(outputs.data, 1)
    predicted = list(predicted.cpu().numpy())
    y_pred += predicted
    
    # probability for each label
    prob = list(outputs.cpu().numpy())
    y_prob += prob


# calculating overall accuracy
num_correct = 0

for i in range(len(y_true)):
  if y_true[i] == y_pred[i]:
    num_correct += 1

print("Accuracy: ", num_correct/len(y_true))



model.eval()
# Convert a list of tuples to two lists
test_X, test_Y = map(list, zip(*test_data))
test_bg = dgl.batch(test_X)
test_Y = torch.tensor(test_Y).float().view(-1, 1)
probs_Y = torch.softmax(model(test_bg), 1)
sampled_Y = torch.multinomial(probs_Y, 1)
argmax_Y = torch.max(probs_Y, 1)[1].view(-1, 1)
print('Accuracy of sampled predictions on the test set: {:.4f}%'.format(
    (test_Y == sampled_Y.float()).sum().item() / len(test_Y) * 100))
print('Accuracy of argmax predictions on the test set: {:4f}%'.format(
    (test_Y == argmax_Y.float()).sum().item() / len(test_Y) * 100))

'''
