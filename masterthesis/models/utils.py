import os
from pathlib import Path

from keras.models import Model
import numpy as np
from tqdm import tqdm

from masterthesis.gensim_utils import load_embeddings
from masterthesis.utils import EMB_LAYER_NAME


def init_pretrained_embs(model: Model, vector_path: Path, w2i) -> None:
    if not vector_path.is_file():
        if 'SUBMITDIR' in os.environ:
            vector_path = Path(os.environ['SUBMITDIR']) / vector_path
        print('New path: %r' % vector_path)
    if not vector_path.is_file():
        print('Embeddings path not available, searching for submitdir')
    else:
        kv = load_embeddings(vector_path)
        embed_dim = kv.vector_size
        emb_layer = model.get_layer(EMB_LAYER_NAME)
        vocab_size = emb_layer.input_dim
        assert embed_dim == emb_layer.output_dim
        assert len(w2i) == vocab_size
        embeddings_matrix = np.zeros((vocab_size, embed_dim))
        print('Making embeddings:')
        for word, idx in tqdm(w2i.items(), total=vocab_size):
            vec = kv.word_vec(word)
            embeddings_matrix[idx, :] = vec
        emb_layer.set_weights([embeddings_matrix])