import argparse
import os
from pathlib import Path
import tempfile

from keras import backend as K
from keras.layers import (
    Activation, Bidirectional, Concatenate, Dense, Dropout, Embedding, Flatten, GRU, Input, Lambda,
    Layer, LSTM, Multiply, Permute, RepeatVector, TimeDistributed
)
from keras.models import Model
from keras.optimizers import RMSprop
from keras.utils import to_categorical
import numpy as np
from tqdm import tqdm

from masterthesis.features.build_features import (
    make_pos2i, make_w2i, pos_to_sequences, words_to_sequences
)
from masterthesis.gensim_utils import load_embeddings
from masterthesis.models.callbacks import F1Metrics
from masterthesis.models.layers import GlobalAveragePooling1D
from masterthesis.models.report import report
from masterthesis.results import save_results
from masterthesis.utils import (
    ATTENTION_LAYER, DATA_DIR, get_file_name, load_split, REPRESENTATION_LAYER, save_model
)


conll_folder = DATA_DIR / 'conll'

SEQ_LEN = 700  # 95th percentile of documents
INPUT_DROPOUT = 0.5
RECURRENT_DROPOUT = 0.1
POS_EMB_DIM = 10
EMB_LAYER_NAME = 'embedding_layer'


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attention', action="store_true")
    parser.add_argument('--batch-size', type=int)
    parser.add_argument('--bidirectional', action="store_true")
    parser.add_argument('--decay-rate', type=float)
    parser.add_argument('--dropout-rate', type=float)
    parser.add_argument('--embed-dim', type=int)
    parser.add_argument('--epochs', type=int)
    parser.add_argument('--fasttext', action="store_true", help='Initialize embeddings')
    parser.add_argument('--freeze-embeddings', action='store_true')
    parser.add_argument('--include-pos', action='store_true')
    parser.add_argument('--lr', type=float)
    parser.add_argument('--nli', action="store_true", help='Classify NLI')
    parser.add_argument('--rnn-cell', choices={'gru', 'lstm'})
    parser.add_argument('--rnn-dim', type=int)
    parser.add_argument('--round-cefr', action='store_true')
    parser.add_argument('--save-model', action='store_true')
    parser.add_argument('--vectors', type=Path, help='Embedding vectors')
    parser.add_argument('--vocab-size', type=int)
    parser.set_defaults(batch_size=32, decay_rate=0.9, dropout_rate=0.5, embed_dim=50, epochs=50,
                        lr=1e-3, rnn_cell='lstm', rnn_dim=300, vocab_size=4000)
    return parser.parse_args()


def _build_inputs_and_embeddings(vocab_size: int, sequence_len: int, embed_dim: int,
                                 mask_zero: bool, trainable_embeddings: bool, num_pos: int):
    word_input_layer = Input((sequence_len,))
    word_embedding_layer = Embedding(
        vocab_size, embed_dim, mask_zero=mask_zero, name=EMB_LAYER_NAME,
        trainable=trainable_embeddings)(word_input_layer)
    if num_pos > 0:
        pos_input_layer = Input((sequence_len,))
        pos_embedding_layer = Embedding(num_pos, POS_EMB_DIM)(pos_input_layer)
        embedding_layer = Concatenate()([word_embedding_layer, pos_embedding_layer])
        inputs = [word_input_layer, pos_input_layer]
    else:
        embedding_layer = word_embedding_layer
        inputs = [word_input_layer]
    return inputs, embedding_layer


def _build_rnn(rnn_cell: str, rnn_dim: int, bidirectional: bool) -> Layer:
    if rnn_cell == 'lstm':
        cell_factory = LSTM
    elif rnn_cell == 'gru':
        cell_factory = GRU
    rnn_factory = cell_factory(rnn_dim, return_sequences=True, dropout=INPUT_DROPOUT,
                               recurrent_dropout=RECURRENT_DROPOUT)
    if bidirectional:
        rnn_factory = Bidirectional(rnn_factory)
    return rnn_factory


def build_model(vocab_size: int, sequence_len: int, num_classes: int,
                embed_dim: int, rnn_dim: int, dropout_rate: float,
                bidirectional: bool, attention: bool, freeze_embeddings: bool, rnn_cell: str,
                num_pos: int = 0):
    mask_zero = not attention  # The attention mechanism does not support masked inputs
    trainable_embeddings = not freeze_embeddings

    inputs, embedding_layer = _build_inputs_and_embeddings(
        vocab_size, sequence_len, embed_dim, mask_zero, trainable_embeddings, num_pos)

    rnn_factory = _build_rnn(rnn_cell, rnn_dim, bidirectional)
    rnn = rnn_factory(embedding_layer)

    dropout = Dropout(dropout_rate)(rnn)

    if attention:
        units = 2 * rnn_dim if bidirectional else rnn_dim
        # compute importance for each step
        attention = TimeDistributed(Dense(1, activation='tanh'))(dropout)
        attention = Flatten()(attention)
        attention = Activation('softmax', name=ATTENTION_LAYER)(attention)
        attention = RepeatVector(units)(attention)
        attention = Permute([2, 1])(attention)

        # apply the attention
        sent_representation = Multiply()([dropout, attention])
        pooled = Lambda(lambda xin: K.sum(xin, axis=1),
                        name=REPRESENTATION_LAYER)(sent_representation)
    else:
        pooled = GlobalAveragePooling1D(name=REPRESENTATION_LAYER)(dropout)

    output = Dense(num_classes, activation='softmax')(pooled)
    return Model(inputs=inputs, outputs=[output])


def main():
    args = parse_args()
    train_meta = load_split('train', round_cefr=args.round_cefr)
    dev_meta = load_split('dev', round_cefr=args.round_cefr)

    vocab_size = args.vocab_size
    w2i = make_w2i(vocab_size)
    train_x, dev_x = words_to_sequences(SEQ_LEN, ['train', 'dev'], w2i)

    target_col = 'lang' if args.nli else 'cefr'
    labels = sorted(train_meta[target_col].unique())

    train_y = to_categorical([labels.index(c) for c in train_meta[target_col]])
    dev_y = to_categorical([labels.index(c) for c in dev_meta[target_col]])

    if args.include_pos:
        pos2i = make_pos2i()
        num_pos = len(pos2i)
        train_pos, dev_pos = pos_to_sequences(SEQ_LEN, ['train', 'dev'], pos2i)
        train_x = [train_x, train_pos]
        dev_x = [dev_x, dev_pos]
    else:
        num_pos = 0

    model = build_model(
        vocab_size=vocab_size, sequence_len=SEQ_LEN, num_classes=len(labels),
        embed_dim=args.embed_dim, rnn_dim=args.rnn_dim, dropout_rate=args.dropout_rate,
        bidirectional=args.bidirectional, attention=args.attention,
        freeze_embeddings=args.freeze_embeddings, rnn_cell=args.rnn_cell, num_pos=num_pos)
    model.summary()

    if args.vectors:
        if not args.vectors.is_file():
            if 'SUBMITDIR' in os.environ:
                args.vectors = Path(os.environ['SUBMITDIR'] / args.vectors)
            print('New path: %r' % args.vectors)
        if not args.vectors.is_file():
            print('Embeddings path not available, searching for submitdir')
        else:
            kv = load_embeddings(args.vectors, fasttext=args.fasttext)
            embeddings_matrix = np.zeros((vocab_size, args.embed_dim))
            print('Making embeddings:')
            for word, idx in tqdm(w2i.items(), total=len(w2i)):
                vec = kv.word_vec(word)
                embeddings_matrix[idx, :] = vec
            model.get_layer(EMB_LAYER_NAME).set_weights([embeddings_matrix])

    model.compile(
        optimizer=RMSprop(lr=args.lr, rho=args.decay_rate),
        loss='categorical_crossentropy',
        metrics=['accuracy'])

    # Context manager fails on Windows (can't open an open file again)
    temp_handle, weights_path = tempfile.mkstemp(suffix='.h5')
    callbacks = [F1Metrics(dev_x, dev_y, weights_path)]
    history = model.fit(
        train_x, train_y, epochs=args.epochs, batch_size=args.batch_size,
        callbacks=callbacks, validation_data=(dev_x, dev_y),
        verbose=2)
    model.load_weights(weights_path)
    os.close(temp_handle)
    os.remove(weights_path)

    name = 'rnn'
    if args.nli:
        name += '_nli'
    name = get_file_name(name)

    if args.save_model:
        save_model(name, model, w2i)

    predictions = model.predict(dev_x)

    true = np.argmax(dev_y, axis=1)
    pred = np.argmax(predictions, axis=1)
    report(true, pred, labels)
    save_results(name, args.__dict__, history.history, true, pred)


if __name__ == '__main__':
    main()
