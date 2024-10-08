import os

import pytorch_lightning
import pytorch_lightning as pl
import yaml
# from DeepPurpose.encoders import *
# import DeepPurpose.DTI as models
from timm import create_model

from torch import Tensor
from metabci.brainda.algorithms.deep_learning import *
from metabci.brainda.algorithms.deep_learning.encoders.LaBraM.modeling_finetune import *
from metabci.brainda.algorithms.deep_learning.utils import load_model_labram



class Classifier(nn.Sequential):
    def __init__(self, model, **config):
        super(Classifier, self).__init__()
        if config['encoder'] in ['convca', 'deepnet', 'eegnet', 'guney_net', 'shallownet', 'labram']:
            self.model = model
            self.dropout = nn.Dropout(config.get('dropout', 0.1))
            self.predictor = nn.ModuleList([nn.Identity()])
        else:
            self.input_dim = config['dim_representation']
            self.model = model
            self.dropout = nn.Dropout(config.get('dropout', 0.1))
            self.hidden_dims = config['cls_hidden_dims']
            layer_size = len(self.hidden_dims) + 1
            dims = [self.input_dim] + self.hidden_dims + [config['n_classes']]
            self.predictor = nn.ModuleList([nn.Linear(dims[i], dims[i + 1]) for i in range(layer_size)])

    def forward(self, v, **params):
        v = self.model(v)
        v = self.head_forward(v)
        return v
    def head_forward(self, v, **params):
        for i, l in enumerate(self.predictor):
            if i == (len(self.predictor) - 1):
                v = l(v)
                v = F.gelu(v)
            else:
                v = F.gelu(self.dropout(l(v)))  # you can add softmax here
        return v

    def predict(self, v):
        self.eval()
        y = self(v)
        return y


def model_initialize(**config):
    model = EEG_model(**config)
    model.initialize()
    return model


def model_pretrained(**config):
    model = EEG_model(**config)
    model.initialize()
    if config['encoder'] == "labram":
        load_model_labram(config['pretrained_path'], model.module.encoder)
    else:
        raise AttributeError('This pretrained model getting method has not been implemented yet.')
    return model

@SkorchNet
class EEG_model(pytorch_lightning.LightningModule):
    def __init__(self, **config):
        super().__init__()
        print(config)
        encoder = config['encoder']
        print(encoder)
        self.encoder_name = encoder
        if encoder == 'convca':
            self.encoder = ConvCA(n_channels=config['n_channels'],
                                  n_samples=config['n_samples'],
                                  n_classes=config['n_classes']).module
        elif encoder == 'deepnet':
            self.encoder = Deep4Net(n_channels=config['n_channels'],
                                    n_samples=config['n_samples'],
                                    n_classes=config['n_classes']).module
        elif encoder == 'eegnet':
            self.encoder = EEGNet(n_channels=config['n_channels'],
                                  n_samples=config['n_samples'],
                                  n_classes=config['n_classes']).module
        elif encoder == "guney_net":
            self.encoder = GuneyNet(n_channels=config['n_channels'],
                                    n_samples=config['n_samples'],
                                    n_classes=config['n_classes'],
                                    n_bands=config['n_bands']).module
        elif encoder == "shallownet":
            self.encoder = ShallowNet(n_channels=config['n_channels'],
                                      n_samples=config['n_samples'],
                                      n_classes=config['n_classes']).module
        elif encoder == "transformer":
            self.encoder = transformer(**config)
        elif encoder == "CNN":
            self.encoder = CNN(**config)
        elif encoder == "CNN_RNN":
            self.encoder = CNN_RNN(**config)
        elif encoder == "MLP":
            self.encoder = MLP(input_dim=config['input_dim'],
                               output_dim=config['output_dim'],
                               hidden_dims_lst=config['hidden_dims_lst'])
        elif encoder == "labram":
            yaml_path = config["yaml_path"]
            with open(yaml_path, 'r') as file:
                config_from_yaml = yaml.safe_load(file)
                config_from_yaml['model_config'].update(config)
                if "num_classes" in config_from_yaml['model_config']:
                    config_from_yaml['model_config']['num_classes'] = config['n_classes']
            self.encoder = create_model(**config_from_yaml['model_config'])
        else:
            raise AttributeError('Please use one of the available encoding method.')

        self.model = Classifier(self.encoder, **config)

        self.config = config

        self.result_folder = config.get('result_folder', "./result")
        if not os.path.exists(self.result_folder):
            os.mkdir(self.result_folder)
        if config['n_classes'] == 2:
            self.binary = True
            self.multiclass = False
        elif config['n_classes'] > 2:
            self.binary = False
            self.multiclass = True
        else:
            self.binary = False
            self.multiclass = False

        if 'num_workers' not in self.config.keys():
            self.config['num_workers'] = 0
        if 'decay' not in self.config.keys():
            self.config['decay'] = 0

    def forward(self, x, **params):
        x = self.model(x, **params)
        return x

    def cal_backbone(self, X: Tensor, **kwargs):
        existing_encoders = ['convca', 'deepnet', 'eegnet', 'guney_net', 'shallownet']
        if self.encoder_name in existing_encoders:
            tmp = self.encoder.cal_backbone(X, **kwargs)
            tmp = self.model.head_forward(tmp, **kwargs)
            return tmp
        else:
            tmp = self.model(X)
            return tmp

if __name__ == '__main__':
    ### 此文件工作目录请切换到项目根目录 ###
    # for test eegnet
    data = np.random.random((1, 32, 200)).astype(np.float64)
    config = {"encoder":"eegnet",
              "n_channels":32,
              "n_samples":200,
              "n_classes":3}
    model = model_initialize(**config)

    model.initialize()
    res = model.predict(data)
    print(res)

    # for test labram
    data = np.random.random((1, 30, 1, 200)).astype(np.float64)
    LOAD_PRETRAINED_MODEL = True
    if LOAD_PRETRAINED_MODEL:
        config = {"encoder": "labram",
                  "n_channels": 32,
                  "n_samples": 200,
                  "n_classes": 3,
                  "pretrained_path": "checkpoints/LaBraM/labram-base.pth",
                  "yaml_path": "checkpoints/config.yaml",
                  'input_channels': np.arange(31)}
        model = model_pretrained(**config)
    else:
        config = {"encoder": "labram",
                  "n_channels": 32,
                  "n_samples": 200,
                  "n_classes": 3,
                  "yaml_path": "checkpoints/config.yaml",
                  'input_channels': np.arange(31)}
        model = model_initialize(**config)

    res = model.predict(data)
    print(res)
