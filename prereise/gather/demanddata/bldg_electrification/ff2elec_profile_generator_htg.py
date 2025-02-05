import os

import numpy as np
import pandas as pd

from prereise.gather.demanddata.bldg_electrification import const


def calculate_cop(temp_c, model):
    cop_base, cr_base = _calculate_cop_base_cr_base(temp_c, model)

    eaux = [max(0.75 - i, 0) for i in cr_base]

    sumlist = [
        (cr_base[i] + eaux[i]) / (cr_base[i] / cop_base[i] + eaux[i])
        if cr_base[i] != 0
        else 1
        for i in range(len(cr_base))
    ]
    cop = [1 if cr_base[i] == 0 else max(sumlist[i], 1) for i in range(len(cr_base))]
    return cop


def _calculate_cop_base_cr_base(temp_c, model):
    temp_k = [i + 273.15 for i in temp_c]
    cop_base = [0] * len(temp_c)
    cr_base = [0] * len(temp_c)

    model_params = const.hp_param.set_index("model").loc[model]
    T1_K = model_params.loc["T1_K"]  # noqa: N806
    COP1 = model_params.loc["COP1"]  # noqa: N806
    T2_K = model_params.loc["T2_K"]  # noqa: N806
    COP2 = model_params.loc["COP2"]  # noqa: N806
    T3_K = model_params.loc["T3_K"]  # noqa: N806
    COP3 = model_params.loc["COP3"]  # noqa: N806
    CR3 = model_params.loc["CR3"]  # noqa: N806
    a = model_params.loc["a"]
    b = model_params.loc["b"]
    c = model_params.loc["c"]

    for i, temp in enumerate(temp_k):
        if temp + b > 0:
            cr_base[i] = a * np.log(temp + b) + c
        if temp > T2_K:
            cop_base[i] = ((COP1 - COP2) / (T1_K - T2_K)) * temp + (
                COP2 * T1_K - COP1 * T2_K
            ) / (T1_K - T2_K)
        if T3_K < temp <= T2_K:
            cop_base[i] = ((COP2 - COP3) / (T2_K - T3_K)) * temp + (
                COP3 * T2_K - COP2 * T3_K
            ) / (T2_K - T3_K)
        if temp <= T3_K:
            cop_base[i] = (cr_base[i] / CR3) * COP3

    return cop_base, cr_base


def htg_to_cop(temp_c, model):
    if model == "futurehp":
        cop = calculate_cop(temp_c, model)

        adv_cop = calculate_cop(temp_c, "advperfhp")
        cop_final = [max(cop[i], adv_cop[i]) for i in range(len(cop))]
        return cop_final
    else:
        return calculate_cop(temp_c, model)


def generate_htg_profiles(
    yr_temps=const.base_year,
    states=None,
    bldg_class="res",
    hp_model="advperfhp",
    output_folder="Profiles",
):
    """Generate and write profiles on dist.
    Create time series for electricity loads from converting
    fossil fuel heating to electric heat pumps.

    :param int yr_temps: year for temperature, defaults to ``const.base_year``.
    :param list states: list of states to loop through, defaults to None, in which
        case ``const.state_list`` is used.
    :param str bldg_class: type of building.
    :param str hp_model: type of heat pump.
    :param str output_folder: location to store profiles (will be created if necessary).
    :raises TypeError:
        if ``yr_temps`` is not an int.
        if ``bldg_class`` and ``hp_model`` are not str.
    :raises ValueError:
        if ``yr_temps`` is not one of the available temperature data year
        if ``bldg_class`` is not 'res' or 'com'
        if ``hp_model`` is not 'advperfhp', 'midperfhp' or 'futurehp'
    """
    if not isinstance(yr_temps, int):
        raise TypeError("yr_temps must be an int")
    if states is None:
        states = const.state_list
    if not isinstance(bldg_class, str):
        raise TypeError("bldg_class must be a str")
    if not isinstance(hp_model, str):
        raise TypeError("hp_model must be a str")

    if yr_temps not in const.yr_temps_all:
        raise ValueError(
            "yr_temps must be among available temperature years: "
            "{const.yr_temps_first}-{const.yr_temps_last}"
        )
    if bldg_class not in ["res", "com"]:
        raise ValueError(
            "bldg_class must be one of: \n", "res: residential \n", "com: commercial"
        )
    if hp_model not in ["advperfhp", "midperfhp", "futurehp"]:
        raise ValueError(
            "hp_model must be one of: \n",
            "midperfhp: mid-performance cold climate heat pump \n",
            "advperfhp: advanced performance cold climate heat pump \n",
            "futurehp: future performance heat pump",
        )
    os.makedirs(output_folder, exist_ok=True)

    # parse user data
    temp_ref_it = const.temp_ref[bldg_class]
    dir_path = os.path.dirname(os.path.abspath(__file__))
    puma_slopes = pd.read_csv(
        os.path.join(dir_path, "data", f"puma_slopes_ff_{bldg_class}.csv"),
        index_col="puma",
    )

    # Loop through states to create profile outputs
    for state in states:
        # Load and subset relevant data for the state
        puma_data_it = const.puma_data.query("state == @state")
        puma_slopes_it = puma_slopes.query("state == @state")
        temps_pumas_it = pd.read_csv(
            f"https://besciences.blob.core.windows.net/datasets/bldg_el/pumas/{yr_temps}/temps/temps_pumas_{state}_{yr_temps}.csv"
        )

        # Compute electric HP loads from fossil fuel conversion
        elec_htg_ff2hp_puma_mw_it_ref_temp = temps_pumas_it.applymap(
            lambda x: max(temp_ref_it - x, 0)
        )
        elec_htg_ff2hp_puma_mw_it_func = temps_pumas_it.apply(
            lambda x: np.reciprocal(htg_to_cop(x, hp_model))
        )

        elec_htg_ff2hp_puma_mw_it = elec_htg_ff2hp_puma_mw_it_ref_temp.multiply(
            elec_htg_ff2hp_puma_mw_it_func
        )

        pumalist = (
            puma_slopes_it[f"htg_slope_{bldg_class}_mmbtu_m2_degC"]
            * puma_data_it[f"{bldg_class}_area_{const.base_year}_m2"]
            * puma_data_it[f"frac_ff_sh_{bldg_class}_{const.base_year}"]
            * (const.conv_mmbtu_to_kwh * const.conv_kw_to_mw)
            * const.eff_htg_ff_base
        )

        elec_htg_ff2hp_puma_mw_it *= pumalist

        # Export profile file as CSV
        elec_htg_ff2hp_puma_mw_it.to_csv(
            os.path.join(
                os.path.dirname(__file__),
                "Profiles",
                f"elec_htg_ff2hp_{bldg_class}_{state}_{yr_temps}_{hp_model}_mw.csv",
            ),
            index=False,
        )
