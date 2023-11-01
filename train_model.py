import zuko
from data_generation import generate_data, generate_strain_coefficients, compute_strain_from_coeffs, window_coeffs, perform_window, compute_hTT_coeffs,polynomial_dict
from torch.utils.data import TensorDataset, DataLoader, random_split
import torch
import torch.nn as nn
import numpy as np
import os
import matplotlib.pyplot as plt
import matplotlib.animation as animation
#from make_animations import make_2d_animation, make_2d_distribution, make_3d_animation, make_3d_distribution
import make_animations as animations
import plotting
import json
import argparse
import copy

def train_epoch(dataloader: torch.utils.data.DataLoader, model: torch.nn.Module, pre_model: torch.nn.Module, optimiser: torch.optim, device:str = "cpu", train:bool = True) -> float:
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

def create_model(conv_layers, linear_layers, n_context):
    """create a convolutional to linear model with n_context outputs

    Args:
        conv_layers (_type_): convolutional layers [(input_channels, output_channels, filter_size, max_pool_size), (), ...]
        linear_layers (_type_): fully connected layers [layer1_size, layer2_size, ...]
        n_context (_type_): number of context inputs to flow (output size of this network)

    Returns:
        _type_: _description_
    """
    pre_model = nn.Sequential()

    for lind, layer in enumerate(conv_layers):
        pre_model.add_module(f"conv_{lind}", nn.Conv1d(layer[0], layer[1], layer[2], padding="same"))
        pre_model.add_module(f"relu_{lind}", nn.ReLU())
        if layer[3] > 1:
            pre_model.add_module(f"maxpool_{lind}", nn.MaxPool1d(layer[3]))

    pre_model.add_module("flatten", nn.Flatten())
    
    for lind, layer in enumerate(linear_layers):
        pre_model.add_module(f"lin_{lind}", nn.LazyLinear(layer))

    pre_model.add_module("output", nn.LazyLinear(n_context))

    return pre_model

def load_models(config, device):
    """Load in models from config

    Args:
        config (_type_): config dictionary
        device (_type_): which device to put the models on

    Returns:
        tuple: pre_model, model
    """
    times, labels, strain, cshape, positions = generate_data(2, config["chebyshev_order"], config["n_masses"], config["sample_rate"], n_dimensions=config["n_dimensions"], detectors=config["detectors"], window=config["window"], return_windowed_coeffs=config["return_windowed_coeffs"])

    n_features = cshape*config["n_masses"]*config["n_dimensions"] + config["n_masses"]
    n_context = config["sample_rate"]*2

    pre_model = create_model(config["conv_layers"], config["linear_layers"], n_context).to(device)

    model = zuko.flows.spline.NSF(n_features, context=n_context, transforms=config["ntransforms"], bins=config["nsplines"], hidden_features=config["hidden_features"]).to(device)
    
    weights = torch.load(os.path.join(config["root_dir"],"test_model.pt"), map_location=device)

    pre_model.load_state_dict(weights["pre_model_state_dict"])

    model.load_state_dict(weights["model_state_dict"])

    return pre_model, model

def normalise_data(strain):
    """normalise the data to the maximum strain in all data

    Args:
        strain (_type_): strain array

    Returns:
        _type_: normalised strain
    """
    max_strain = np.max(strain)
    return strain/max_strain

def run_testing(config:dict) -> None:
    """ run testing (loads saved model and runs testing scripts)

    Args:
        config (dict): _description_
    """
    pre_model, model = load_models(config, config["device"])

    times, labels, strain, cshape, positions = generate_data(config["n_test_data"], config["chebyshev_order"], config["n_masses"], config["sample_rate"], n_dimensions=config["n_dimensions"], detectors=config["detectors"], window=config["window"], return_windowed_coeffs=config["return_windowed_coeffs"])

    acc_chebyshev_order = cshape

    n_features = acc_chebyshev_order*config["n_masses"]*config["n_dimensions"] + config["n_masses"]
    n_context = config["sample_rate"]*2

    dataset = TensorDataset(torch.Tensor(labels), torch.Tensor(strain))
    test_loader = DataLoader(dataset, batch_size=1)


    if config["n_dimensions"] == 1:
        test_model_1d(model, test_loader, times, config["n_masses"], config["chebyshev_order"], config["n_dimensions"], config["root_dir"], config["device"])
    elif config["n_dimensions"] == 2:
        test_model_2d(model, pre_model, test_loader, times, config["n_masses"], config["chebyshev_order"], config["n_dimensions"], config["root_dir"], config["device"])
    elif config["n_dimensions"] == 3:
        test_model_3d(model, pre_model, test_loader, times, config["n_masses"], acc_chebyshev_order, config["n_dimensions"], config["detectors"], config["window"], config["root_dir"], config["device"], config["return_windowed_coeffs"])
    

def run_training(config: dict, continue_train:bool = False) -> None:
    """ run the training loop 

    Args:
        root_dir (str): _description_
        config (dict): _description_
    """
    if not os.path.isdir(config["root_dir"]):
        os.makedirs(config["root_dir"])

    with open(os.path.join(config["root_dir"], "config.json"),"w") as f:
        json.dump(config, f)

    times, labels, strain, cshape, positions = generate_data(config["n_data"], config["chebyshev_order"], config["n_masses"], config["sample_rate"], n_dimensions=config["n_dimensions"], detectors=config["detectors"], window=config["window"], return_windowed_coeffs=config["return_windowed_coeffs"])

    #strain = normalise_data(strain)

    plotting.plot_data(times, positions, strain, 10, config["root_dir"])

    acc_chebyshev_order = cshape

    n_features = acc_chebyshev_order*config["n_masses"]*config["n_dimensions"] + config["n_masses"]
    n_context = config["sample_rate"]*2
    print("init", n_features, n_context)

    fig, ax = plt.subplots()
    ax.plot(strain[0])
    fig.savefig(os.path.join(config["root_dir"], "test_data.png"))


    print(np.shape(labels), np.shape(strain))

    dataset = TensorDataset(torch.Tensor(labels), torch.Tensor(strain))
    train_size = int(0.9*config["n_data"])
    #test_size = 10
    train_set, val_set = random_split(dataset, (train_size, config["n_data"] - train_size))
    train_loader = DataLoader(train_set, batch_size=config["batch_size"],shuffle=True)
    val_loader = DataLoader(val_set, batch_size=config["batch_size"]).to(config["device"])
    
    #test_loader = DataLoader(test_set, batch_size=1)

    if continue_train:
        pre_model, model = load_models(config, device=config["device"])
    else:   
        pre_model = create_model(config["conv_layers"], config["linear_layers"], n_context)

        pre_model.to(config["device"])
        
        model = zuko.flows.spline.NSF(n_features, context=n_context, transforms=config["ntransforms"], bins=config["nsplines"], hidden_features=config["hidden_features"]).to(config["device"])
        
    optimiser = torch.optim.AdamW(list(model.parameters()) + list(pre_model.parameters()), lr=config["learning_rate"])

    if continue_train:
        with open(os.path.join(config["root_dir"], "train_losses.txt"), "r") as f:
            losses = np.loadtxt(f)
        train_losses = losses[0]
        val_losses = losses[1]
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
            },
            os.path.join(config["root_dir"],"test_model.pt"))

        fig, ax = plt.subplots()
        ax.plot(train_losses)
        ax.plot(val_losses)
        fig.savefig(os.path.join(config["root_dir"], "lossplot.png"))

    print("Completed Training")


def get_dynamics(coeffmass_samples, times, n_masses, chebyshev_order, n_dimensions, poly_type="chebyshev"):
    """get the dynamics of the system from polynomial cooefficients and masses

    Args:
        coeffmass_samples (_type_): samples of the coefficients and masses
        times (_type_): times when to evaluate the polynomial
        n_masses (_type_): how many masses 
        chebyshev_order (_type_): order of the polynomimal
        n_dimensions (_type_): how many dimensions 

    Returns:
        tuple: (coefficients, masses, timeseries)
    """
    #print("msshape", np.shape(coeffmass_samples))
    masses = coeffmass_samples[-n_masses:]
    coeffs = coeffmass_samples[:-n_masses].reshape(n_masses,chebyshev_order, n_dimensions)

    tseries = np.zeros((n_masses, n_dimensions, len(times)))
    for mass_index in range(n_masses):
        for dim_index in range(n_dimensions):
            tseries[mass_index, dim_index] = polynomial_dict[poly_type]["val"](times, coeffs[mass_index, :, dim_index])

    return coeffs, masses, tseries

def test_model_1d(model, dataloader, times, n_masses, chebyshev_order, n_dimensions, root_dir, device, poly_type="chebyshev"):

    plot_out = os.path.join(root_dir, "testout")
    if not os.path.isdir(plot_out):
        os.makedirs(plot_out)

    model.eval()
    with torch.no_grad():
        for batch, (label, data) in enumerate(dataloader):
            label, data = label.to(device), data.to(device)
            coeffmass_samples = model(data.flatten(start_dim=1)).sample().cpu().numpy()

            source_coeffs, source_masses, source_tseries = get_dynamics(label[0].cpu().numpy(), times, n_masses, chebyshev_order, n_dimensions, poly_type=poly_type)
            recon_coeffs, recon_masses, recon_tseries = get_dynamics(coeffmass_samples[0], times, n_masses, chebyshev_order, n_dimensions, poly_type=poly_type)

            fig, ax = plt.subplots(nrows = 4)
            for mass_index in range(n_masses):
                ax[0].plot(times, source_tseries[mass_index, 0])
                ax[1].plot(times, recon_tseries[mass_index, 0])
                ax[2].plot(times, source_tseries[mass_index, 0] - recon_tseries[mass_index, 0])
    
            recon_weighted_coeffs = np.sum(recon_coeffs[:,0] * recon_masses[:, None], axis=0)
            source_weighted_coeffs = np.sum(source_coeffs[:,0] * source_masses[:, None], axis=0)

            recon_strain_coeffs = generate_strain_coefficients(recon_weighted_coeffs)
            source_strain_coeffs = generate_strain_coefficients(source_weighted_coeffs)

            recon_strain = np.polynomial.chebyshev.chebval(times, recon_strain_coeffs)
            source_strain = np.polynomial.chebyshev.chebval(times, source_strain_coeffs)


            ax[3].plot(times, recon_strain, label="recon")
            ax[3].plot(times, source_strain, label="source")
            ax[3].plot(times, data[0][0].cpu().numpy(), label="source data")

            fig.savefig(os.path.join(plot_out, f"reconstructed_{batch}.png"))


def test_model_2d(model, pre_model, dataloader, times, n_masses, chebyshev_order, n_dimensions, root_dir, device):
    """_summary_

    Args:
        model (_type_): _description_
        pre_model (_type_): _description_
        dataloader (_type_): _description_
        times (_type_): _description_
        n_masses (_type_): _description_
        chebyshev_order (_type_): _description_
        n_dimensions (_type_): _description_
        root_dir (_type_): _description_
        device (_type_): _description_
    """
    plot_out = os.path.join(root_dir, "testout")
    if not os.path.isdir(plot_out):
        os.makedirs(plot_out)

    model.eval()
    with torch.no_grad():
        for batch, (label, data) in enumerate(dataloader):
            label, data = label.to(device), data.to(device)
            input_data = pre_model(data)
            coeffmass_samples = model(input_data).sample().cpu().numpy()

            print(np.shape(coeffmass_samples[0]))
            source_coeffs, source_masses, source_tseries = get_dynamics(label[0].cpu().numpy(), times, n_masses, chebyshev_order, n_dimensions)
            recon_coeffs, recon_masses, recon_tseries = get_dynamics(coeffmass_samples[0], times, n_masses, chebyshev_order, n_dimensions)

            fig, ax = plt.subplots(nrows = 4)
            for mass_index in range(n_masses):
                ax[0].plot(times, source_tseries[mass_index,0], color="k", alpha=0.8)
                ax[0].plot(times, source_tseries[mass_index,1], color="r", alpha=0.8)
                ax[1].plot(times, recon_tseries[mass_index, 0])
                ax[1].plot(times, recon_tseries[mass_index, 1])
                ax[2].plot(times, source_tseries[mass_index,0] - recon_tseries[mass_index,0])
    
            recon_weighted_coeffs = np.sum(recon_coeffs * recon_masses[:, None, None], axis=0)
            source_weighted_coeffs = np.sum(source_coeffs * source_masses[:, None, None], axis=0)

            recon_strain_tensor = generate_2d_derivative(recon_weighted_coeffs, times)
            source_strain_tensor = generate_2d_derivative(source_weighted_coeffs, times)

            recon_strain = recon_strain_tensor[0,0] + recon_strain_tensor[0,1]
            source_strain = source_strain_tensor[0,0] + source_strain_tensor[0,1]

            ax[3].plot(times, recon_strain, label="recon")
            ax[3].plot(times, source_strain, label="source")
            ax[3].plot(times, data[0][0].cpu().numpy(), label="source data")

            fig.savefig(os.path.join(plot_out, f"reconstructed_{batch}.png"))

            make_2d_animation(plot_out, batch, recon_tseries, recon_masses, source_tseries, source_masses)


            nsamples = 50
            multi_coeffmass_samples = model(input_data).sample((nsamples, )).cpu().numpy()

            #print("multishape", multi_coeffmass_samples.shape)
            m_recon_tseries, m_recon_masses = np.zeros((nsamples, n_masses, n_dimensions, len(times))), np.zeros((nsamples, n_masses))
            for i in range(nsamples):
                #print(np.shape(multi_coeffmass_samples[i]))
                t_co, t_mass, t_time = get_dynamics(multi_coeffmass_samples[i][0], times, n_masses, chebyshev_order, n_dimensions)
                m_recon_tseries[i] = t_time
                m_recon_masses[i] = t_mass

            make_2d_distribution(plot_out, batch, m_recon_tseries, m_recon_masses, source_tseries, source_masses)

def test_model_3d(model, pre_model, dataloader, times, n_masses, chebyshev_order, n_dimensions, detectors, window, root_dir, device, return_windowed_coeffs=True, poly_type="chebyshev"):
    """test a 3d model sampling from the flow and producing possible trajectories

        makes animations and plots comparing models

    Args:
        model (_type_): _description_
        pre_model (_type_): _description_
        dataloader (_type_): _description_
        times (_type_): _description_
        n_masses (_type_): _description_
        chebyshev_order (_type_): _description_
        n_dimensions (_type_): _description_
        root_dir (_type_): _description_
        device (_type_): _description_
    """
    plot_out = os.path.join(root_dir, "testout_2")
    if not os.path.isdir(plot_out):
        os.makedirs(plot_out)

    model.eval()
    with torch.no_grad():
        for batch, (label, data) in enumerate(dataloader):
            label, data = label.to(device), data.to(device)
            input_data = pre_model(data)
          
            coeffmass_samples = model(input_data).sample().cpu().numpy()

            source_coeffs, source_masses, source_tseries = get_dynamics(label[0].cpu().numpy(), times, n_masses, chebyshev_order, n_dimensions, poly_type=poly_type)
            recon_coeffs, recon_masses, recon_tseries = get_dynamics(coeffmass_samples[0], times, n_masses, chebyshev_order, n_dimensions, poly_type=poly_type)
        
            # if there is a window and I and not predicting the windowed coefficients
            if not return_windowed_coeffs and window != False:
                n_recon_coeffs = []
                n_source_coeffs = []
                # for each mass perform the window on the xyz positions (acceleration)
                for mass in range(n_masses):
                    temp_recon, win_coeffs = perform_window(times, recon_coeffs[mass], window, poly_type=poly_type)
                    temp_source, win_coeffs = perform_window(times, source_coeffs[mass], window, poly_type=poly_type)
                    n_recon_coeffs.append(temp_recon)
                    n_source_coeffs.append(temp_source)
                
                # update the coefficients with the windowed version
                recon_coeffs = np.array(n_recon_coeffs)
                source_coeffs = np.array(n_source_coeffs)

            recon_strain_coeffs = compute_hTT_coeffs(recon_masses, np.transpose(recon_coeffs, (0,2,1)), poly_type=poly_type)
            source_strain_coeffs = compute_hTT_coeffs(source_masses, np.transpose(source_coeffs, (0,2,1)), poly_type=poly_type)


            recon_strain = []
            source_strain = []
            for detector in detectors:
                recon_strain.append(compute_strain_from_coeffs(times, recon_strain_coeffs, detector=detector, poly_type=poly_type))
                source_strain.append(compute_strain_from_coeffs(times, source_strain_coeffs, detector=detector, poly_type=poly_type))

            fig = plotting.plot_reconstructions(
                            times, 
                            detectors, 
                            recon_strain, 
                            source_strain, 
                            data[0].cpu().numpy(), 
                            fname = os.path.join(plot_out, f"reconstructed_{batch}.png"))

            plotting.plot_positions(
                times, 
                source_tseries, 
                recon_tseries, 
                n_dimensions, 
                n_masses,
                fname = os.path.join(plot_out, f"positions_{batch}.png"))

            plotting.plot_z_projection(
                source_tseries, 
                recon_tseries, 
                fname = os.path.join(plot_out,f"z_projection_{batch}.png"))


            animations.make_3d_animation(
                plot_out, 
                batch, 
                recon_tseries, 
                recon_masses, 
                source_tseries, 
                source_masses)

            nsamples = 500
            n_animate_samples = 50
            multi_coeffmass_samples = model(input_data).sample((nsamples, )).cpu().numpy()

            #print("multishape", multi_coeffmass_samples.shape)
            m_recon_tseries, m_recon_masses = np.zeros((nsamples, n_masses, n_dimensions, len(times))), np.zeros((nsamples, n_masses))
            for i in range(nsamples):
                #print(np.shape(multi_coeffmass_samples[i]))
                t_co, t_mass, t_time = get_dynamics(multi_coeffmass_samples[i][0], times, n_masses, chebyshev_order, n_dimensions, poly_type=poly_type)
                m_recon_tseries[i] = t_time
                m_recon_masses[i] = t_mass
            
            if n_masses == 2:
                print(np.shape(m_recon_masses))
                neginds = m_recon_masses[:,0] - m_recon_masses[:,1] < 0

                new_recon_tseries = copy.copy(m_recon_tseries)
                new_recon_tseries[neginds, 0] = m_recon_tseries[neginds, 1]
                new_recon_tseries[neginds, 1] = m_recon_tseries[neginds, 0]

                new_recon_masses = copy.copy(m_recon_masses)
                new_recon_masses[neginds, 0] = m_recon_masses[neginds, 1]
                new_recon_masses[neginds, 1] = m_recon_masses[neginds, 0]

                m_recon_masses = new_recon_masses
                m_recon_tseries = new_recon_tseries


                if source_masses[0] - source_masses[1] < 0:
                    new_source_tseries = copy.copy(source_tseries)
                    new_source_tseries[0] = source_tseries[1]
                    new_source_tseries[1] = source_tseries[0]

                    new_source_masses = copy.copy(source_masses)
                    new_source_masses[0] = source_masses[1]
                    new_source_masses[1] = source_masses[0]

                    source_masses = new_source_masses
                    source_tseries = new_source_tseries
            

            plotting.plot_sample_separations(
                times, 
                source_tseries, 
                m_recon_tseries, 
                fname=os.path.join(plot_out,f"separations_{batch}.png"))

            plotting.plot_mass_distributions(
                m_recon_masses,
                source_masses,
                fname=os.path.join(plot_out,f"massdistributions_{batch}.png"))

            
            animations.line_of_sight_animation(
                m_recon_tseries, 
                m_recon_masses, 
                source_tseries, 
                source_masses, 
                os.path.join(plot_out,f"2d_massdist_{batch}.gif"))


            animations.make_3d_distribution(
                plot_out, 
                batch, 
                m_recon_tseries[:n_animate_samples], 
                m_recon_masses[:n_animate_samples], 
                source_tseries, 
                source_masses)

            animations.make_3d_distribution_zproj(
                plot_out, 
                batch, 
                m_recon_tseries, 
                m_recon_masses, 
                source_tseries, 
                source_masses)

def project_to_line_of_sight(coeffs):
    pass



if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument("-c", "--config", type=str, required=False, default="none")
    parser.add_argument("--train", type=bool, required=False, default=False)
    parser.add_argument("--test", type=bool, required=False, default=False)
    parser.add_argument("--continuetrain", type=bool, required=False, default=False)
    args = parser.parse_args()

    if args.config == "none":
        config = dict(
            n_data = 600000,
            n_test_data = 10,
            batch_size = 2048,
            chebyshev_order = 20,
            n_masses = 1,
            n_dimensions = 3,
            detectors=["H1", "L1", "V1"],
            conv_layers = [(3, 64, 16, 1),(64, 32, 16, 1), (32, 32, 4, 1), (32, 32, 4, 2), (32, 32, 4, 2), ],
            linear_layers = [512, 256, 256],
            sample_rate = 128,
            n_epochs = 2000,
            window=False,
            poly_type="chebyshev",
            return_windowed_coeffs=False,
            learning_rate = 1e-3,
            device = "cuda:0",
            nsplines = 8,
            ntransforms = 8,
            hidden_features = [512, 512, 512],
            root_dir = "test_1mass_cheb16_3d_3det_nowindowpost_morelin_batch2048"
        )
    else:
        with open(os.path.abspath(args.config), "r") as f:
            config = json.load(f)

    continue_train = args.continuetrain
    train_model = args.train
    test_model = args.test

    if train_model:
        run_training(config, continue_train=continue_train)

    if test_model:
        run_testing(config)
