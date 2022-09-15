import os
import platform
import shutil
import tempfile
import unittest
from functools import partial

import torch
from test.common.torchtext_test_case import TorchtextTestCase
from torch.utils.data import DataLoader
from torchtext.data.functional import custom_replace
from torchtext.prototype.transforms import (
    basic_english_normalize,
    PRETRAINED_SP_MODEL,
    sentencepiece_processor,
    sentencepiece_tokenizer,
    VocabTransform,
)
from torchtext.prototype.vectors import build_vectors, FastText, GloVe, load_vectors_from_file_path
from torchtext.prototype.vocab_factory import build_vocab_from_text_file, load_vocab_from_file
from torchtext.utils import download_from_url

from ..common.assets import get_asset_path


# Windows and MaxOS doesn't support the nested function pickle
# Move the batch function out of the test_sentencepiece_with_dataloader test
def _batch_func(spm_processor, data):
    return torch.tensor([spm_processor(text) for text in data], dtype=torch.long)


class TestTransformsWithAsset(TorchtextTestCase):
    def test_vocab_transform(self) -> None:
        asset_name = "vocab_test2.txt"
        asset_path = get_asset_path(asset_name)
        vocab_transform = VocabTransform(load_vocab_from_file(asset_path))
        self.assertEqual(vocab_transform(["of", "that", "new"]), [7, 18, 24])
        jit_vocab_transform = torch.jit.script(vocab_transform)
        self.assertEqual(jit_vocab_transform(["of", "that", "new", "that"]), [7, 18, 24, 18])

    def test_errors_vectors_python(self) -> None:
        tokens = []
        vecs = torch.empty(0, dtype=torch.float)

        with self.assertRaises(ValueError):
            # Test proper error raised when passing in empty tokens and vectors and
            # not passing in a user defined unk_tensor
            build_vectors(tokens, vecs)

        tensorA = torch.tensor([1, 0, 0], dtype=torch.int8)
        tokens = ["a"]
        vecs = tensorA.unsqueeze(0)

        with self.assertRaises(TypeError):
            # Test proper error raised when vector is not of type torch.float
            build_vectors(tokens, vecs)

        with tempfile.TemporaryDirectory() as dir_name:
            # Test proper error raised when incorrect filename or dim passed into GloVe
            asset_name = "glove.6B.zip"
            asset_path = get_asset_path(asset_name)
            data_path = os.path.join(dir_name, asset_name)
            shutil.copy(asset_path, data_path)

            with self.assertRaises(ValueError):
                # incorrect name
                GloVe(name="UNK", dim=50, root=dir_name, validate_file=False)

            with self.assertRaises(ValueError):
                # incorrect dim
                GloVe(name="6B", dim=500, root=dir_name, validate_file=False)

    def test_glove(self) -> None:
        # copy the asset file into the expected download location
        # note that this is just a zip file with the first 100 entries of the GloVe 840B dataset
        asset_name = "glove.840B.300d.zip"
        asset_path = get_asset_path(asset_name)

        with tempfile.TemporaryDirectory() as dir_name:
            data_path = os.path.join(dir_name, asset_name)
            shutil.copy(asset_path, data_path)
            vectors_obj = GloVe(root=dir_name, validate_file=False)
            jit_vectors_obj = torch.jit.script(vectors_obj)

            # The first 3 entries in each vector.
            expected_glove = {
                "the": [0.27204, -0.06203, -0.1884],
                "people": [-0.19686, 0.11579, -0.41091],
            }

            for word in expected_glove.keys():
                self.assertEqual(vectors_obj[word][:3], expected_glove[word])
                self.assertEqual(jit_vectors_obj[word][:3], expected_glove[word])

    def test_glove_different_dims(self) -> None:
        # copy the asset file into the expected download location
        # note that this is just a zip file with 1 line txt files used to test that the
        # correct files are being loaded
        asset_name = "glove.6B.zip"
        asset_path = get_asset_path(asset_name)

        with tempfile.TemporaryDirectory() as dir_name:
            data_path = os.path.join(dir_name, asset_name)
            shutil.copy(asset_path, data_path)

            glove_50d = GloVe(name="6B", dim=50, root=dir_name, validate_file=False)
            glove_100d = GloVe(name="6B", dim=100, root=dir_name, validate_file=False)
            glove_200d = GloVe(name="6B", dim=200, root=dir_name, validate_file=False)
            glove_300d = GloVe(name="6B", dim=300, root=dir_name, validate_file=False)
            vectors_objects = [glove_50d, glove_100d, glove_200d, glove_300d]

            # The first 3 entries in each vector.
            expected_glove_50d = {
                "the": [0.418, 0.24968, -0.41242],
            }
            expected_glove_100d = {
                "the": [-0.038194, -0.24487, 0.72812],
            }
            expected_glove_200d = {
                "the": [-0.071549, 0.093459, 0.023738],
            }
            expected_glove_300d = {
                "the": [0.04656, 0.21318, -0.0074364],
            }
            expected_gloves = [expected_glove_50d, expected_glove_100d, expected_glove_200d, expected_glove_300d]

            for vectors_obj, expected_glove in zip(vectors_objects, expected_gloves):
                for word in expected_glove.keys():
                    self.assertEqual(vectors_obj[word][:3], expected_glove[word])

    def test_vocab_from_file(self) -> None:
        asset_name = "vocab_test.txt"
        asset_path = get_asset_path(asset_name)
        v = load_vocab_from_file(asset_path)
        expected_itos = ["b", "a", "c"]
        expected_stoi = {x: index for index, x in enumerate(expected_itos)}
        self.assertEqual(v.get_itos(), expected_itos)
        self.assertEqual(dict(v.get_stoi()), expected_stoi)

    # TODO(Nayef211): remove decorator once https://github.com/pytorch/text/issues/1900 is closed
    @unittest.skipIf("CI" in os.environ and platform.system() == "Linux", "Test is known to fail on Linux.")
    def test_vocab_from_raw_text_file(self) -> None:
        asset_name = "vocab_raw_text_test.txt"
        asset_path = get_asset_path(asset_name)

        def python_basic_english_normalize(input):
            patterns_list = [
                (r"\'", " '  "),
                (r"\"", ""),
                (r"\.", " . "),
                (r"<br \/>", " "),
                (r",", " , "),
                (r"\(", " ( "),
                (r"\)", " ) "),
                (r"\!", " ! "),
                (r"\?", " ? "),
                (r"\;", " "),
                (r"\:", " "),
                (r"\s+", " "),
            ]
            norm_transform = custom_replace(patterns_list)
            return list(norm_transform([input.lower()]))[0].split()

        # using python based basic_english_normalize tokenizer
        # we can also use basic_english_normalize() here
        v1 = build_vocab_from_text_file(asset_path, tokenizer=python_basic_english_normalize)
        expected_itos = [
            "'",
            "after",
            "talks",
            ".",
            "are",
            "at",
            "disappointed",
            "fears",
            "federal",
            "firm",
            "for",
            "mogul",
            "n",
            "newall",
            "parent",
            "pension",
            "representing",
            "say",
            "stricken",
            "t",
            "they",
            "turner",
            "unions",
            "with",
            "workers",
        ]
        expected_stoi = {x: index for index, x in enumerate(expected_itos)}
        self.assertEqual(v1.get_itos(), expected_itos)
        self.assertEqual(dict(v1.get_stoi()), expected_stoi)

        # using JIT'D basic_english_normalize tokenizer
        v2 = build_vocab_from_text_file(asset_path, tokenizer=torch.jit.script(basic_english_normalize()))
        self.assertEqual(v2.get_itos(), expected_itos)
        self.assertEqual(dict(v2.get_stoi()), expected_stoi)

    def test_builtin_pretrained_sentencepiece_processor(self) -> None:
        sp_model_path = download_from_url(PRETRAINED_SP_MODEL["text_unigram_25000"])
        spm_tokenizer = sentencepiece_tokenizer(sp_model_path)
        _path = os.path.join(self.project_root, ".data", "text_unigram_25000.model")
        os.remove(_path)
        test_sample = "the pretrained spm model names"
        ref_results = ["\u2581the", "\u2581pre", "trained", "\u2581sp", "m", "\u2581model", "\u2581names"]
        self.assertEqual(spm_tokenizer(test_sample), ref_results)

        sp_model_path = download_from_url(PRETRAINED_SP_MODEL["text_bpe_25000"])
        spm_transform = sentencepiece_processor(sp_model_path)
        _path = os.path.join(self.project_root, ".data", "text_bpe_25000.model")
        os.remove(_path)
        test_sample = "the pretrained spm model names"
        ref_results = [13, 1465, 12824, 304, 24935, 5771, 3776]
        self.assertEqual(spm_transform(test_sample), ref_results)

    # we separate out these errors because Windows runs into seg faults when propagating
    # exceptions from C++ using pybind11
    def test_sentencepiece_with_dataloader(self) -> None:
        example_strings = ["the pretrained spm model names"] * 64
        ref_results = torch.tensor([[13, 1465, 12824, 304, 24935, 5771, 3776]] * 16, dtype=torch.long)

        sp_model_path = download_from_url(PRETRAINED_SP_MODEL["text_bpe_25000"])
        spm_processor = sentencepiece_processor(sp_model_path)
        batch_fn = partial(_batch_func, spm_processor)

        dataloader = DataLoader(example_strings, batch_size=16, num_workers=2, collate_fn=batch_fn)
        for item in dataloader:
            self.assertEqual(item, ref_results)

    def test_vectors_from_file(self) -> None:
        asset_name = "vectors_test.csv"
        asset_path = get_asset_path(asset_name)
        vectors_obj = load_vectors_from_file_path(asset_path)

        expected_tensorA = torch.tensor([1, 0, 0], dtype=torch.float)
        expected_tensorB = torch.tensor([0, 1, 0], dtype=torch.float)
        expected_unk_tensor = torch.tensor([0, 0, 0], dtype=torch.float)

        self.assertEqual(vectors_obj["a"], expected_tensorA)
        self.assertEqual(vectors_obj["b"], expected_tensorB)
        self.assertEqual(vectors_obj["not_in_it"], expected_unk_tensor)

    def test_fast_text(self) -> None:
        # copy the asset file into the expected download location
        # note that this is just a file with the first 100 entries of the FastText english dataset
        asset_name = "wiki.en.vec"
        asset_path = get_asset_path(asset_name)

        with tempfile.TemporaryDirectory() as dir_name:
            data_path = os.path.join(dir_name, asset_name)
            shutil.copy(asset_path, data_path)
            vectors_obj = FastText(root=dir_name, validate_file=False)
            jit_vectors_obj = torch.jit.script(vectors_obj)

            # The first 3 entries in each vector.
            expected_fasttext_simple_en = {
                "the": [-0.065334, -0.093031, -0.017571],
                "world": [-0.32423, -0.098845, -0.0073467],
            }

            for word in expected_fasttext_simple_en.keys():
                self.assertEqual(vectors_obj[word][:3], expected_fasttext_simple_en[word])
                self.assertEqual(jit_vectors_obj[word][:3], expected_fasttext_simple_en[word])
