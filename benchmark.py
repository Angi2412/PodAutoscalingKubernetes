# Copyright (c) 2020 Angelina Horn
from gevent import monkey

monkey.patch_all()

import gevent
from locust.env import Environment
from locust.stats import stats_history, StatsCSVFileWriter
from data.loadtest.teastore import UserBehavior
# imports
import datetime as dt
import logging
import os
import time
import json

from dotenv import load_dotenv, set_key

from prometheus_api_client import PrometheusConnect, MetricRangeDataFrame, MetricSnapshotDataFrame

import numpy as np
import pandas as pd
import k8s_tools as k8s
import requests

# environment
load_dotenv(override=True)

# init logger
logging.getLogger().setLevel(logging.INFO)


def config_env(**kwargs) -> None:
    """
    Configures the environment file.
    :param kwargs: keys and values to be set.
    :return: None
    """
    arguments = locals()
    env_file = os.path.join(os.getcwd(), ".env")
    for i in arguments["kwargs"].keys():
        key = str(i).upper()
        value = str(arguments["kwargs"][i])
        set_key(dotenv_path=env_file, key_to_set=key, value_to_set=value)


def get_prometheus_data(folder: str, iteration: int) -> None:
    """
    Exports metric data from prometheus to a csv file.
    :param folder: save folder
    :param iteration: number of current iteration
    :return: None
    """
    # metrics to export
    resource_metrics = [
        "kube_pod_container_resource_requests_memory_bytes",
        "kube_pod_container_resource_limits_memory_bytes",
        "kube_pod_container_resource_limits_cpu_cores",
        "kube_pod_container_resource_requests_cpu_cores",
        "container_cpu_cfs_throttled_seconds_total",
        "kube_deployment_spec_replicas"
    ]
    network_metrics = ["response_latency_ms_sum", "response_latency_ms_count"]
    # get resource metric data resources
    resource_metrics_data = get_prometheus_metric(metric_name=resource_metrics[0], mode="RESOURCES", custom=False)
    for x in range(1, len(resource_metrics)):
        resource_metrics_data = resource_metrics_data + get_prometheus_metric(metric_name=resource_metrics[x],
                                                                              mode="RESOURCES", custom=False)
    # get custom resource metric data resources
    custom_memory = get_prometheus_metric(metric_name="memory", mode="RESOURCES", custom=True)
    custom_memory = MetricRangeDataFrame(custom_memory)
    custom_memory.insert(0, 'metric', "memory")
    custom_cpu = get_prometheus_metric(metric_name="cpu", mode="RESOURCES", custom=True)
    custom_cpu = MetricRangeDataFrame(custom_cpu)
    custom_cpu.insert(0, 'metric', "cpu")
    # get network metric data
    network_metrics_data = get_prometheus_metric(metric_name=network_metrics[0],
                                                 mode="NETWORK", custom=False) + get_prometheus_metric(
        metric_name=network_metrics[1],
        mode="NETWORK", custom=False)
    # convert to dataframe
    metrics_data = resource_metrics_data + network_metrics_data
    metric_df = MetricRangeDataFrame(metrics_data)
    custom_metrics_df = pd.concat([custom_cpu, custom_memory])
    # write to csv file
    metric_df.to_csv(rf"{folder}\metrics_{iteration}.csv")
    custom_metrics_df.to_csv(rf"{folder}\custom_metrics_{iteration}.csv")


def get_status(pod: str) -> (list, list):
    # init
    prom_res = PrometheusConnect(url=os.getenv(f'PROMETHEUS_RESOURCES_HOST'), disable_ssl=True)
    prom_net = PrometheusConnect(url=os.getenv(f'PROMETHEUS_NETWORK_HOST'), disable_ssl=True)
    # custom queries
    cpu_usage = '(sum(rate(container_cpu_usage_seconds_total{namespace="teastore", container!=""}[5m])) by (pod, ' \
                'container) /sum(container_spec_cpu_quota{namespace="teastore", ' \
                'container!=""}/container_spec_cpu_period{namespace="teastore", container!=""}) by (pod, ' \
                'container) )*100'
    memory_usage = 'round(max by (pod)(max_over_time(container_memory_usage_bytes{namespace="teastore",pod=~".*" }[' \
                   '5m]))/ on (pod) (max by (pod) (kube_pod_container_resource_limits)) * 100,0.01)'

    # target metrics
    cpu_usage_data = MetricSnapshotDataFrame(prom_res.custom_query(cpu_usage))
    cpu_usage_data["pod"] = cpu_usage_data["pod"].str.split("-", n=2).str[1]
    memory_usage_data = MetricSnapshotDataFrame(prom_res.custom_query(memory_usage))
    memory_usage_data["pod"] = memory_usage_data["pod"].str.split("-", n=2).str[1]
    average_response_time_data = MetricSnapshotDataFrame(
        prom_net.get_current_metric_value("response_latency_ms_sum") / prom_net.get_current_metric_value(
            "response_latency_ms_count"))
    average_response_time_data["pod"] = average_response_time_data["pod"].str.split("-", n=2).str[1]
    # filter
    cpu_usage = cpu_usage_data.loc[(cpu_usage_data['pod'] == pod)].at[0, 'value']
    memory_usage = memory_usage_data.loc[(memory_usage_data['pod'] == pod)].at[0, 'value']
    average_response_time = average_response_time_data.loc[(average_response_time_data['pod'] == pod)].at[0, 'value']
    targets = [cpu_usage, memory_usage, average_response_time]
    # parameter metrics
    cpu_limit_data = MetricSnapshotDataFrame(
        prom_res.get_current_metric_value("kube_pod_container_resource_limits_cpu_cores"))
    cpu_limit_data["pod"] = cpu_limit_data["pod"].str.split("-", n=2).str[1]
    memory_limit_data = MetricSnapshotDataFrame(
        prom_res.get_current_metric_value("kube_pod_container_resource_limits_memory_bytes"))
    memory_limit_data["pod"] = memory_limit_data["pod"].str.split("-", n=2).str[1]
    number_of_pods_data = MetricSnapshotDataFrame(prom_res.get_current_metric_value("kube_deployment_spec_replicas"))
    number_of_pods_data["pod"] = number_of_pods_data["pod"].str.split("-", n=2).str[1]
    # filter
    cpu_limit = cpu_limit_data.loc[(cpu_limit_data['pod'] == pod)].at[0, 'value']
    memory_limit = memory_limit_data.loc[(memory_limit_data['pod'] == pod)].at[0, 'value']
    number_of_pods = number_of_pods_data.loc[(number_of_pods_data['pod'] == pod)].at[0, 'value']
    parameters = [cpu_limit, memory_limit, number_of_pods]
    return parameters, targets


def get_prometheus_metric(metric_name: str, mode: str, custom: bool) -> list:
    """
    Gets a given metric from prometheus in a given timeframe.
    :param custom: if custom query should be used
    :param mode: which prometheus to use
    :param metric_name: name of the metric
    :return: metric
    """
    # init
    prom = PrometheusConnect(url=os.getenv(f'PROMETHEUS_{mode}_HOST'), disable_ssl=True)
    start_time = (dt.datetime.now() - dt.timedelta(hours=int(os.getenv("HH")), minutes=int(os.getenv("MM"))))
    # custom queries
    cpu_usage = '(sum(rate(container_cpu_usage_seconds_total{namespace="teastore", container!=""}[5m])) by (pod, ' \
                'container) /sum(container_spec_cpu_quota{namespace="teastore", ' \
                'container!=""}/container_spec_cpu_period{namespace="teastore", container!=""}) by (pod, ' \
                'container) )*100'
    memory_usage = 'round(max by (pod)(max_over_time(container_memory_usage_bytes{namespace="teastore",pod=~".*" }[' \
                   '5m]))/ on (pod) (max by (pod) (kube_pod_container_resource_limits)) * 100,0.01)'
    query = None
    # get data
    if custom:
        if metric_name == "cpu":
            query = cpu_usage
        elif metric_name == "memory":
            query = memory_usage
        else:
            logging.error("Accepts cpu or memory, but received " + metric_name)
        metric_data = prom.custom_query_range(
            query=query,
            start_time=start_time,
            end_time=dt.datetime.now(),
            step="10")
    else:
        metric_data = prom.get_metric_range_data(
            metric_name=metric_name,
            start_time=start_time,
            end_time=dt.datetime.now(),
        )
    return metric_data


def benchmark(name: str, users: int, spawn_rate: int, expressions: int,
              step: int, pods_limit: int, run: int, run_max: int) -> None:
    """
    Benchmark methods.
    :param run_max: number of runs
    :param run: current run
    :param expressions: number of expressions per parameter
    :param pods_limit: pods limit
    :param step: size of step
    :param name: name of ms
    :param users: number of users
    :param spawn_rate: spawn rate
    :return: None
    """
    # init date
    date = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    # create folder
    folder_path = os.path.join(os.getcwd(), "data", "raw", date)
    os.mkdir(folder_path)
    # create deployment
    k8s.k8s_create_teastore()
    # config
    set_key(dotenv_path=os.path.join(os.getcwd(), ".env"), key_to_set="LAST_DATA", value_to_set=date)
    k8s.set_prometheus_info()
    config_env(app_name=name,
               host=os.getenv("HOST"),
               node_port=k8s.k8s_get_app_port(),
               date=date,
               users=users,
               spawn_rate=spawn_rate,
               )
    # read new environment data
    load_dotenv(override=True)

    # get variation
    variations = parameter_variation_namespace(pods_limit, expressions, step)
    c_max, m_max, p_max = variations[os.getenv("UI")].shape
    iteration = 1
    scale_only = "webui"
    # benchmark
    logging.info("Starting Benchmark.")
    for c in range(0, c_max):
        for m in range(0, m_max):
            for p in range(0, p_max):
                logging.info(
                    f"Iteration: {iteration}/{c_max * m_max * p_max} run: {run}/ {run_max}")

                # for every pod in deployment
                for pod in variations.keys():
                    # check that pod is scalable
                    if scale_only in pod:
                        # get parameter variation
                        v = variations[pod][c, m, p]
                        logging.info(f"{pod}: cpu: {int(v[0])}m - memory: {int(v[1])}Mi - # pods: {int(v[2])}")
                        # update resources of pod
                        k8s.k8s_update_deployment(deployment_name=pod, cpu_limit=int(v[0]),
                                                  memory_limit=int(v[1]),
                                                  number_of_replicas=int(v[2]), replace=True)
                    # wait for deployment
                    time.sleep(120)
                    while not k8s.check_teastore_health():
                        time.sleep(10)
                # start load test
                logging.info("Start Locust.")
                start_locust(iteration=iteration, folder=folder_path, history=False)
                # get prometheus data
                get_prometheus_data(folder=folder_path, iteration=iteration)
                iteration = iteration + 1
    # delete namespace
    k8s.k8s_delete_namespace()
    logging.info("Finished Benchmark.")


def parameter_variation_namespace(pods_limit: int, expressions: int, step: int) -> dict:
    """
    Generates the parameter variation matrix for every deployment in a namespace with given values.
    :param pods_limit: pod limit
    :param expressions: number of expressions
    :param step: size of step
    :return: dict of parameter variation matrices
    """
    resource_requests = k8s.get_resource_requests()
    variation = dict()
    for p in resource_requests.keys():
        if p == os.getenv("SCALE_POD"):
            logging.debug("Pod: " + p)
            # cpu
            p_cpu_request = int(resource_requests[p]["cpu"].split("m")[0]) + 100
            p_cpu_limit = p_cpu_request + (expressions * step)
            logging.debug(f"cpu request: {p_cpu_request}m - cpu limit: {p_cpu_limit}m")
            # memory
            p_memory_request = int(resource_requests[p]["memory"].split("Mi")[0]) + 100
            p_memory_limit = p_memory_request + (expressions * step)
            logging.debug(f"memory request: {p_memory_request}Mi - memory limit: {p_memory_limit}Mi")
            # parameter variation matrix
            variation[p] = parameter_variation(p, p_cpu_request, p_cpu_limit, p_memory_request,
                                               p_memory_limit, pods_limit, step, invert=True)
    return variation


def parameter_variation(pod: str, cpu_request: int, cpu_limit: int, memory_request: int, memory_limit: int,
                        pods_limit: int, step: int, invert: bool) -> np.array:
    """
    Calculates a matrix mit all combination of the parameters.
    :return: parameter variation matrix
    """
    # init parameters: (start, end, step)
    cpu = np.arange(cpu_request, cpu_limit, step, np.int32)
    memory = np.arange(memory_request, memory_limit, step, np.int32)
    pods = np.arange(1, pods_limit + 1, 1, np.int32)
    if invert:
        cpu = np.flip(cpu)
        memory = np.flip(memory)
        pods = np.flip(pods)
    iterations = np.arange(1, (cpu.size * memory.size * pods.size) + 1, 1).tolist()

    # init dataframe
    df = pd.DataFrame(index=iterations, columns=["CPU", "Memory", "Pods"])
    csv_path = os.path.join(os.getcwd(), "data", "raw", os.getenv("LAST_DATA"), f"{pod}_variation.csv")
    # init matrix
    variation_matrix = np.zeros((cpu.size, memory.size, pods.size),
                                dtype=[('cpu', np.int32), ('memory', np.int32), ('pods', np.int32)])
    # fill matrix
    i = 1
    for c in range(0, cpu.size):
        for m in range(0, memory.size):
            for p in range(0, pods.size):
                variation_matrix[c, m, p] = (cpu[c], memory[m], pods[p])
                # fill dataframe
                df.at[i, 'CPU'] = cpu[c]
                df.at[i, 'Memory'] = memory[m]
                df.at[i, 'Pods'] = pods[p]
                i = i + 1
    logging.debug(df.head())
    # save dataframe to csv
    if not os.path.exists(csv_path):
        df.to_csv(csv_path)
    return variation_matrix


def start_locust(iteration: int, folder: str, history: bool) -> None:
    """
    Start a locust load test.
    :param iteration: number of current iteration
    :param folder: name of folder
    :param history: enables stats
    :return: None
    """
    load_dotenv(override=True)
    # setup Environment and Runner
    env = Environment(user_classes=[UserBehavior],
                      host=f"http://{os.getenv('HOST')}:{os.getenv('NODE_PORT')}/{os.getenv('ROUTE')}")
    env.create_local_runner()
    # CSV writer
    stats_path = os.path.join(folder, f"locust_{iteration}")
    if history:
        csv_writer = StatsCSVFileWriter(
            environment=env,
            base_filepath=stats_path,
            full_history=True,
            percentiles_to_report=[90.0, 50.0]
        )
        # start a greenlet that save current stats to history
        gevent.spawn(stats_history, env.runner)
        # spawn csv writer
        gevent.spawn(csv_writer)
    # start the test
    env.runner.start(user_count=int(os.getenv("USERS")), spawn_rate=int(os.getenv("SPAWN_RATE")))
    # stop the runner in a given time
    time_in_seconds = (int(os.getenv("HH")) * 60 * 60) + (int(os.getenv("MM")) * 60)
    gevent.spawn_later(time_in_seconds, lambda: env.runner.quit())
    # wait for the greenlets
    env.runner.greenlet.join()


def get_persistence_data() -> None:
    """
    Gets persistence data from the TeaStore.
    :return: None
    """
    base_path = os.path.join(os.getcwd(), "data", "loadtest")
    persistence_url = "http://localhost:30090/tools.descartes.teastore.persistence/rest"
    # get category ids
    categories_request = requests.get(persistence_url + "/categories").json()
    tmp_categories = list()
    for c in categories_request:
        tmp_categories.append(c["id"])
    with open(os.path.join(base_path, "categories.json"), 'x') as outfile:
        json.dump(tmp_categories, outfile)
    # get product ids
    products_request = requests.get(persistence_url + "/products").json()
    tmp_products = list()
    for p in products_request:
        tmp_products.append(p["id"])
    with open(os.path.join(base_path, "products.json"), 'x') as outfile:
        json.dump(tmp_products, outfile)
    # get users
    users = requests.get(persistence_url + "/users").json()
    with open(os.path.join(base_path, "users.json"), 'x') as outfile:
        json.dump(users, outfile)


def start_run(name: str, users: int, spawn_rate: int, expressions: int, step: int, pods_limit: int, runs: int):
    date = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    set_key(dotenv_path=os.path.join(os.getcwd(), ".env"), key_to_set="FIRST_DATA", value_to_set=date)
    for i in range(1, runs + 1):
        benchmark(name, users, spawn_rate, expressions, step, pods_limit, i, runs)

