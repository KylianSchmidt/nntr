import numpy as np
import awkward as ak
import pandas as pd
import uproot
import sys
import itertools


class PrepareInputs:
    def __init__(self, filename):
        self.filename = filename.rstrip('.root')

        hits, hits_normalized, hits_mean, hits_std = self._find_hits()
        truth_normalized, truth_mean, truth_std = self._find_truth()
        offsets = self.offsets
        offsets_cumsum = np.append(0, np.cumsum(offsets))
        print("Length of truth", len(truth_normalized))
        print("Length of offsets", len(offsets))
        print("Length of offsets_cumsum", len(offsets_cumsum))


        # with uproot.recreate(f"{self.filename}_preprocessed.root") as file:
        #     file["Hits"] = {"hits": hits, "hits_normalized": hits_normalized}
        #     file["Hits_offsets_cumsum"] = {"offsets_cumsum": offsets_cumsum}
        #     file["Hits_offsets"] = {"offsets": offsets}
        #     file["Hits_parameters"] = {"hits_mean": hits_mean, "hits_std": hits_std}
        #     file["Truth"] = {"truth_normalized": truth_normalized}
        #     file["Truth_parameters"] = {"truth_mean": truth_mean, "truth_std": truth_std}

    def _extract_to_array(self, key) -> ak.Array:
        """ This method extracts the events and hits for a given key in the root
        tree

        Returns shapes:
        hits x [layerType, x, y, z, E]
        """
        with uproot.open(self.filename+".root") as data:
            return data[key].array(library="ak")[1000:2000]

    def _find_hits(self) -> np.ndarray:
        keys = ["layerType", "cellID", "x", "y", "z", "E"]
        prefix = "Events/Output/fHits/fHits."
        raw_data_dict = {}

        print("[1/3] Extract from root file")
        for keys in keys:
            raw_data_dict[keys] = self._extract_to_array(prefix+keys)

        raw_arr = []
        for key in raw_data_dict.keys():
            raw_arr.append(raw_data_dict[key][..., np.newaxis])
        raw_arr = ak.concatenate(raw_arr, axis=-1)

        print("[2/3] Convert to DataFrame and sum over same cellIDs (will take approx. " +
              f"{0.7/100*len(raw_data_dict['layerType']):.0f} seconds)")

        offsets = ak.count(raw_arr, axis=1)[:, 0]

        # Create dataFrame (it is faster to create the pd.MultiIndex by hand than using
        # ak.to_dafaframe)
        i_0 = list(np.repeat(np.arange(len(offsets)), offsets))
        i_1 = list(itertools.chain.from_iterable(
            [(np.arange(i)) for i in ak.to_list(offsets)]))
        nindex = pd.MultiIndex.from_arrays(
            arrays=[i_0, i_1], names=["event", "hit"])
        df_original = pd.DataFrame(
            ak.flatten(raw_arr),
            columns=["layerType", "cellID", "x", "y", "z", "E"],
            index=nindex)

        # Sum over same cellIDs in the calorimeter
        df_calo = df_original[df_original["layerType"] == 2]
        df_calo_grouped = df_calo.groupby(["event", "cellID"])[
            ["layerType", "x", "y", "z"]].first()
        print("Length of df_calo_grouped", len(df_calo_grouped))
        df_calo_grouped["E"] = df_calo.groupby(["event", "cellID"])["E"].sum()
        print("Length of df_calo_grouped after sum", len(df_calo_grouped))

        self.offsets_for_truth_empty_events = ak.to_numpy(offsets)

        print("[3/3] Concatenate DataFrames")
        df = pd.concat(
            [df_original[df_original["layerType"] == 1], df_calo_grouped])
        self.offsets = self._find_offsets(df)

        # Remove cellID as an entry and normalize hits
        hits = np.delete(df.to_numpy(), 1, axis=1)
        mean = np.mean(hits, axis=0)+1E-10
        std = np.std(hits, axis=0)+1E-10
        hits_normalized = (hits-mean)/std

        return hits, hits_normalized, mean, std

    def _find_truth(self) -> np.ndarray:
        prefix = "Events/Output/fParticles/fParticles."
        keys = ["mcPx", "mcPy", "mcPz", "decayX", "decayY", "decayZ"]
        truth = []

        for key in keys:
            truth_single = self._extract_to_array(prefix+key)[..., np.newaxis]

            truth.append(truth_single)

        truth_array = ak.concatenate(truth, axis=-1)
        truth_array = ak.flatten(truth_array, axis=-1)
        truth_array = np.array(ak.to_list(truth_array))

        truth_mean = np.mean(truth_array, axis=0)
        truth_std = np.std(truth_array, axis=0)

        return truth_array, truth_mean, truth_std

    def _find_offsets(self, df: pd.DataFrame) -> np.ndarray:
        return df.reset_index(level=1).index.value_counts().sort_index().to_numpy()


if __name__ == "__main__":
    filename = sys.argv[1]
    PrepareInputs(filename)
