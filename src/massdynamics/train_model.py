import zuko
from massdynamics.data_generation import (
    data_generation,
    compute_waveform,
    data_processing
)
from massdynamics.basis_functions import basis
import massdynamics.create_model
from massdynamics.plotting import plotting, make_animations
from scipy import signal
from massdynamics.test_model import run_testing
from torch.utils.data import TensorDataset, DataLoader, random_split
import torch
import torch.nn as nn
import numpy as np
import os
import matplotlib.pyplot as plt
import massdynamics.plotting as plotting
import json
import argparse
import copy

def train_epoch(
    dataloader: torch.utils.data.DataLoader, 
    model: torch.nn.Module, 
    pre_model: torch.nn.Module, 
    optimiser: torch.optim, 
    device:str = "cpu", 
    train:bool = True) -> float:
    """train one epoch for data

    Args:
        dataloader (torch.DataLoader): dataloader object for all data
        model (torch.Module): pytorch model
        optimiser (torch.optim): pytorch optimiser
        device (str, optional): which device to place model and data Defaults to "cpu".
        train (bool, optional): whether to update model weights or not. Defaults to True.

    Returns:
        float: the average loss over the epoch
    """
    if train:
        model.train()
    else:
        model.eval()

    train_loss = 0
    for batch, (label, data) in enumerate(dataloader):
        label, data = label.to(device), data.to(device)

        optimiser.zero_grad()
        input_data = pre_model(data)
        loss = -model(input_data).log_prob(label).mean()

        if train:
            loss.backward()
            optimiser.step()

        train_loss += loss.item()

    return train_loss/len(dataloader)

    

def run_training(config: dict, continue_train:bool = False) -> None:
    """ run the training loop 

    Args:
        root_dir (str): _description_
        config (dict): _description_
    """
    if not os.path.isdir(config["root_dir"]):
        os.makedirs(config["root_dir"])

    with open(os.path.join(config["root_dir"], "config.json"),"w") as f:
        configstring = json.dumps(config, indent=4)
        f.write(configstring)

    if config["load_data"]:
        print("loading data ........")
        times, labels, strain, cshape, positions = data_generation.load_data(
            data_dir = config["data_dir"], 
            basis_order = config["basis_order"],
            n_masses = config["n_masses"],
            sample_rate = config["sample_rate"],
            n_dimensions = config["n_dimensions"],
            detectors = config["detectors"],
            window = config["window"],
            return_windowed_coeffs = config["return_windowed_coeffs"],
            basis_type = config["basis_type"],
            data_type = config["data_type"]
            )

        config["n_data"] = len(labels)
    else:
        print("making data ........")
        times, labels, strain, cshape, positions, all_dynamics = data_generation.generate_data(
            n_data=config["n_data"], 
            basis_order=config["basis_order"], 
            n_masses=config["n_masses"], 
            sample_rate=config["sample_rate"], 
            n_dimensions=config["n_dimensions"], 
            detectors=config["detectors"], 
            window=config["window"], 
            return_windowed_coeffs=config["return_windowed_coeffs"],
            basis_type = config["basis_type"],
            data_type = config["data_type"],
            fourier_weight=config["fourier_weight"])

    acc_basis_order = cshape

    #window = signal.windows.tukey(np.shape(strain)[-1], alpha=0.5)
    #strain = strain * window[None, None, :]

    n_features = cshape*config["n_masses"]*config["n_dimensions"] + config["n_masses"]
    n_context = 2*config["sample_rate"]

    fig, ax = plt.subplots()
    ax.plot(strain[0])
    fig.savefig(os.path.join(config["root_dir"], "test_data.png"))


    if continue_train:
        pre_model, model = create_model.load_models(config, device=config["device"])
        strain, norm_factor = data_generation.normalise_data(strain, pre_model.norm_factor)
    else:   
        pre_model, model = create_model.create_models(config, device=config["device"])

        pre_model.to(config["device"])
        model.to(config["device"])
        
        strain, norm_factor = data_generation.normalise_data(strain, None)
        pre_model.norm_factor = norm_factor

    plotting.plot_data(times, positions, strain, 10, config["root_dir"])

    dataset = TensorDataset(torch.from_numpy(labels).to(torch.float32), torch.Tensor(strain))

    train_size = int(0.9*config["n_data"])
    #test_size = 10
    train_set, val_set = random_split(dataset, (train_size, config["n_data"] - train_size))
    train_loader = DataLoader(train_set, batch_size=config["batch_size"],shuffle=True)
    val_loader = DataLoader(val_set, batch_size=config["batch_size"])

    optimiser = torch.optim.AdamW(list(model.parameters()) + list(pre_model.parameters()), lr=config["learning_rate"])

    if continue_train:
        with open(os.path.join(config["root_dir"], "train_losses.txt"), "r") as f:
            losses = np.loadtxt(f)
        train_losses = list(losses[0])
        val_losses = list(losses[1])
        start_epoch = len(train_losses)
    else:
        train_losses = []
        val_losses = []
        start_epoch = 0

    print("Start training")
    for epoch in range(config["n_epochs"]):
        if continue_train:
            epoch = epoch + start_epoch

        train_loss = train_epoch(train_loader, model, pre_model, optimiser, device=config["device"], train=True)
        train_losses.append(train_loss)

        with torch.no_grad():
            val_loss = train_epoch(val_loader, model, pre_model, optimiser, device=config["device"], train=False)
            val_losses.append(val_loss)
            
        if epoch % 100 == 0:
            print(f"Epoch: {epoch}, Train loss: {train_loss}, Val loss: {val_loss}")

            with open(os.path.join(config["root_dir"], "train_losses.txt"), "w") as f:
                np.savetxt(f, [train_losses, val_losses])

            torch.save({
                "epoch":epoch,
                "model_state_dict": model.state_dict(),
                "pre_model_state_dict": pre_model.state_dict(),
                "optimiser_state_dict":optimiser.state_dict(),
                "norm_factor": pre_model.norm_factor
            },
            os.path.join(config["root_dir"],"test_model.pt"))

        fig, ax = plt.subplots()
        ax.plot(train_losses)
        ax.plot(val_losses)
        fig.savefig(os.path.join(config["root_dir"], "lossplot.png"))

    print("Completed Training")



if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument("-c", "--config", type=str, required=False, default="none")
    parser.add_argument("--train", type=bool, required=False, default=False)
    parser.add_argument("--test", type=bool, required=False, default=False)
    parser.add_argument("--continuetrain", type=bool, required=False, default=False)
    args = parser.parse_args()

    if args.config == "none":
        config = dict(
            n_data = 500000,
            n_test_data = 10,
            batch_size = 1024,
            basis_order = 6,
            n_masses = 2,
            n_dimensions = 3,
            detectors=["H1", "L1", "V1"],
            conv_layers = [(3, 32, 16, 1), (32, 16, 16, 2), (16, 16, 16, 2) ],
            linear_layers = [256, 256, 256],
            sample_rate = 64,
            n_epochs = 4000,
            window="none",
            basis_type="chebyshev",
            custom_flow=True,
            return_windowed_coeffs=False,
            learning_rate = 5e-5,
            device = "cuda:0",
            nsplines = 8,
            ntransforms = 8,
            hidden_features = [256,256,256],
            root_dir = "customflow_test_2mass_cheb6_3d_3det_nowindow_batch1024_lr5e-5"
        )
    else:
        with open(os.path.abspath(args.config), "r") as f:
            config = json.load(f)

    continue_train = args.continuetrain
    train_model = args.train
    test_model = args.test

    if "custom_flow" not in config.keys():
        config["custom_flow"] = False
    if config["window"] == False:
        config["window"] = "none"
    if "data_dir" not in config.keys():
        config["data_dir"] = "./data"
    if "fourier_weight" not in config.keys():
        config["fourier_weight"] = 0.9
        
    if train_model:
        run_training(config, continue_train=continue_train)

    if test_model:
        run_testing(config)