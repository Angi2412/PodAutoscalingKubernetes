from gevent import monkey

monkey.patch_all()
import os

import pandas as pd
from dotenv import load_dotenv
import seaborn as sns
import logging
import re
from benchmark import parameter_variation
import numpy as np
import matplotlib.pyplot as plt
from functools import reduce

# init
load_dotenv()
# init logger
logging.getLogger().setLevel(logging.INFO)


def get_all_data() -> list:
    # init
    all_data = list()
    for d in get_directories():
        p_data, c_data, l_data = get_data(d)
        # append to list
        all_data.append([p_data, c_data, l_data])
    return all_data


def get_data(directory: str) -> (pd.DataFrame, pd.DataFrame, pd.DataFrame):
    """
    Gets data from prometheus.
    :return: prometheus data
    """
    # config
    load_dotenv(override=True)
    prometheus_data = None
    prometheus_custom_data = None
    locust_data = None
    i, j, l = 0, 0, 0
    # check if folder exists
    data_path = os.path.join(os.getcwd(), "data", "raw", directory)
    if os.path.exists(data_path):
        # search for prometheus metric files
        logging.info(f"Gets data from {directory}.")
        for (dir_path, dir_names, filenames) in os.walk(data_path):
            for file in filenames:
                if "metrics" in file and "custom_metrics" not in file:
                    i = i + 1
                    prometheus_data = get_data_helper(prometheus_data, file, i, directory)
                elif "custom_metrics" in file:
                    j = j + 1
                    prometheus_custom_data = get_data_helper(prometheus_custom_data, file, j, directory)
                elif "locust" in file and "stats" in file and "history" not in file:
                    l = l + 1
                    locust_data = get_data_helper(locust_data, file, l, directory)
    return prometheus_data, prometheus_custom_data, locust_data


def get_data_helper(data: pd.DataFrame, file: str, iteration: int, directory: str) -> pd.DataFrame:
    data_path = os.path.join(os.getcwd(), "data", "raw", directory)
    # concat metrics
    tmp_data = pd.read_csv(filepath_or_buffer=os.path.join(data_path, file), delimiter=',')
    tmp_data.insert(0, 'Iteration', iteration)
    if data is None:
        data = tmp_data
    else:
        data = pd.concat([data, tmp_data])
    return data


def get_directories() -> list:
    load_dotenv()
    first_date = int(str(os.getenv("FIRST_DATA")).replace('-', "").strip())
    last_date = int(str(os.getenv("LAST_DATA")).replace('-', "").strip())
    base_path = os.path.join(os.getcwd(), "data", "raw")
    dirs = list()
    # get data from each run
    for (dir_path, dir_names, filenames) in os.walk(base_path):
        for c_dir in dir_names:
            c_date = int(str(c_dir).replace('-', "").strip())
            if last_date >= c_date >= first_date:
                dirs.append(c_dir)
    return dirs


def get_filtered_data() -> list:
    load_dotenv()
    first_date = int(str(os.getenv("FIRST_DATA")).replace('-', "").strip())
    last_date = int(str(os.getenv("LAST_DATA")).replace('-', "").strip())
    base_path = os.path.join(os.getcwd(), "data", "filtered")
    files = list()
    # get data from each run
    for (dir_path, dir_names, filenames) in os.walk(base_path):
        for c_file in filenames:
            if str(c_file).endswith(".csv"):
                c_date = int(str(c_file).replace('-', "").replace("_filtered.csv", "").strip())
                if last_date >= c_date >= first_date:
                    files.append(c_file)
    return files


def filter_all_data() -> None:
    # init
    i = 1
    dirs = get_directories()
    for d in dirs:
        # filter data in directory
        logging.info(f"Filtering data: {d} {i}/{len(dirs)}")
        filter_data(d)
        i = i + 1


def get_variation_matrices(directory: str) -> pd.DataFrame:
    """
    Reads all variation matrices of a directory and puts them in a list.
    :param directory: current directory
    :return: list of variation matrices
    """
    dir_path = os.path.join(os.getcwd(), "data", "raw", directory)
    variations = list()
    for (dir_path, dir_names, filenames) in os.walk(dir_path):
        for file in filenames:
            if "variation" in file:
                name = str(file).split("_")[0]
                file_path = os.path.join(dir_path, file)
                tmp = pd.read_csv(filepath_or_buffer=file_path, delimiter=',')
                tmp.insert(0, 'pod', name)
                tmp.rename(columns={"Unnamed: 0": "Iteration"}, inplace=True)
                tmp.reset_index()
                variations.append(tmp)
    res = pd.concat(variations)
    return res


def filter_data(directory: str) -> pd.DataFrame:
    """
    Filters data from prometheus.
    :return: filtered data
    """
    normal, custom, locust = get_data(directory)
    # filter by namespace
    filtered_data = pd.concat(objs=[normal[normal.namespace.eq(os.getenv("NAMESPACE"))]])
    # read variation matrices
    variations = get_variation_matrices(directory)
    # filter pod name
    filtered_data["pod"] = filtered_data["pod"].str.split("-", n=1, expand=True)
    custom["pod"] = custom["pod"].str.split("-", n=1, expand=True)
    # create pivot tables
    filtered_data = pd.pivot_table(filtered_data, index=["Iteration", "pod"], columns=["__name__"],
                                   values="value").reset_index()
    filtered_custom_data = pd.pivot_table(custom, index=["Iteration", "pod"], columns=["metric"],
                                          values="value").reset_index()
    # calculate mean values
    filtered_data = filtered_data.groupby(["Iteration", "pod"]).mean()
    filtered_custom_data = filtered_custom_data.groupby(["Iteration", "pod"]).mean()
    filtered_custom_data = filtered_custom_data.reset_index()
    filtered_data = filtered_data.reset_index()
    # fill result
    res_data = pd.merge(filtered_data, filtered_custom_data, how='left', on=["Iteration", "pod"])
    res_data = pd.merge(res_data, variations, how='left', on=["Iteration", "pod"])
    # calc average response time
    res_data["average response time"] = res_data["response_latency_ms_sum"] / res_data["response_latency_ms_count"]
    # erase stuff
    res_data = res_data[res_data['pod'].ne("prometheus")]
    res_data.drop(columns=["kube_deployment_spec_replicas", "kube_pod_container_resource_limits_cpu_cores",
                           "kube_pod_container_resource_limits_memory_bytes",
                           "kube_pod_container_resource_requests_cpu_cores",
                           "kube_pod_container_resource_requests_memory_bytes", "response_latency_ms_count",
                           "response_latency_ms_sum"], inplace=True)
    res_data.rename(
        columns={"cpu": "cpu usage", "memory": "memory usage", "CPU": "cpu limit", "Memory": "memory limit",
                 "Pods": "number of pods", "container_cpu_cfs_throttled_seconds_total": "cpu throttled total"},
        inplace=True)
    save_data(res_data, directory, "filtered")
    return res_data


def save_data(data: pd.DataFrame, directory: str, mode: str) -> None:
    save_path = os.path.join(os.getcwd(), "data", mode, f"{directory}_filtered.csv")
    if not os.path.exists(save_path):
        data.to_csv(path_or_buf=save_path)
    else:
        logging.warning("Filtered data already exists.")


def plot_filtered_data() -> None:
    """
    Plots a given metric from filtered data from prometheus.
    :return: None
    """
    # init
    data = filter_data(directory=os.getenv("LAST_DATA"))
    # create directory
    dir_path = os.path.join(os.getcwd(), "data", "plots", f"{os.getenv('LAST_DATA')}")
    os.mkdir(dir_path)
    # create and save plots
    for metric in data:
        line_plot = sns.lineplot(data=data, x=data["Iteration"], y=metric, hue="pod")
        line_plot.figure.savefig(os.path.join(dir_path, f"{metric}.png"))
        line_plot.figure.clf()
    # make scatter plot
    g = sns.PairGrid(data)
    g.map(sns.scatterplot)
    g.savefig(os.path.join(dir_path, f"scatterplot.png"))
    g.fig.clf()


def format_for_extra_p() -> None:
    # init
    filtered_base_path = os.path.join(os.getcwd(), "data", "filtered")
    save_path = os.path.join(os.getcwd(), "data", "formatted", os.getenv('LAST_DATA'))
    # create directory if not existing
    if not os.path.exists(save_path):
        os.mkdir(save_path)
    # get variation matrix
    variation = parameter_variation(cpu_limit=int(os.getenv("CPU_LIMIT")), memory_limit=int(os.getenv("MEMORY_LIMIT")),
                                    pods_limit=int(os.getenv("PODS_LIMIT")))
    c_max, m_max, p_max = variation.shape
    # parameter and metrics
    parameter = ["CPU limit", "Memory limit", "Number of pods"]
    metrics = ["Average response time [ms]", "Failures [%]", "Memory usage [%]", "CPU usage [%]"]
    # get all filtered data
    filtered_data = list()
    for f in get_filtered_data():
        filtered_data.append(pd.read_csv(os.path.join(filtered_base_path, f)))
    # write in txt file
    for metric in metrics:
        m_name = (re.sub('[^a-zA-Z0-9 _]', '', metric)).rstrip().replace(' ', '_').lower()
        with open(os.path.join(save_path, f"{os.getenv('LAST_DATA')}_{m_name}_extra-p.txt"), "x") as file:
            # write parameters
            for par in parameter:
                file.write(f"PARAMETER {(re.sub('[^a-zA-Z0-9 _]', '', par)).rstrip().replace(' ', '_').lower()}\n")
            file.write("\n")
            # write coordinates
            # for every iteration
            for c in range(0, c_max):
                for m in range(0, m_max):
                    file.write("POINTS ")
                    for p in range(0, p_max):
                        file.write('( ')
                        for v in variation[c, m, p]:
                            file.write(f"{v} ")
                        file.write(') ')
                    file.write("\n")
            file.write("\n")
            file.write("REGION Test\n")
            file.write(f"METRIC {m_name}\n")
            # write data
            # for every datapoint
            for i in range(0, (filtered_data[0].index.max() + 1)):
                logging.info(f"format data: {i + 1}/{(filtered_data[0].index.max() + 1)}")
                file.write("DATA ")
                # for test purposes
                for f in filtered_data:
                    x = f.loc[f.index == i, metric].iloc[0]
                    file.write(f"{x} ")
                file.write("\n")


def correlation_coefficient_matrix(df: pd.DataFrame, directory: str) -> None:
    """
    Calculates and plots the correlation coefficient matrix for a given dataframe.
    :param directory: save label
    :param df: given dataframe
    :return: None
    """
    corr = df.corr(method="pearson")
    save_data(corr, os.getenv("LAST_DATA"), "correlation")
    # plot correlation
    # Generate a mask for the upper triangle
    mask = np.triu(np.ones_like(corr, dtype=bool))
    # Set up the matplotlib figure
    f, ax = plt.subplots(figsize=(11, 9))
    # Generate a custom diverging colormap
    cmap = sns.color_palette("vlag", as_cmap=True)
    # Draw the heatmap with the mask and correct aspect ratio
    sns.heatmap(corr, mask=mask, cmap=cmap, vmax=.3, center=0,
                square=True, linewidths=.5, cbar_kws={"shrink": .5})
    plt.show()


if __name__ == '__main__':
    plot_filtered_data()
