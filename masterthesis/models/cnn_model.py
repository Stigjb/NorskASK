import argparse
from itertools import chain
from pathlib import Path
from typing import Iterable, List

import numpy as np
import pandas as pd
from keras.models import Model
from keras.layers import Embedding
from keras.layers import (
    Input, Conv1D, Dropout, Dense, GlobalMaxPooling1D, Concatenate, GlobalAveragePooling1D
)
from keras.preprocessing.text import Tokenizer
from keras.preprocessing.sequence import pad_sequences
from keras.utils import to_categorical
from sklearn.metrics import classification_report, confusion_matrix

from masterthesis.results import save_results
from masterthesis.utils import load_train_and_dev, conll_reader, heatmap
from masterthesis.utils import safe_plt as plt


def iter_all_tokens(train) -> Iterable[str]:
    """Yield all tokens"""
    for seq in iter_all_docs(train):
        for token in seq:
            yield token


def iter_all_docs(split: pd.DataFrame, column='UPOS') -> Iterable[List[str]]:
    """Iterate over all docs in the split.

    yields:
        Each document as a list of lists of tuples of the given column
    """
    for filename in split.filename:
        filepath = Path('ASK/conll') / (filename + '.conll')
        cr = conll_reader(filepath, [column], tags=True)
        # Only using a single column, extract the value
        tokens = (tup[0] for tup in chain.from_iterable(cr))
        yield list(tokens)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('target_column', nargs='?', choices=['cefr', 'lang'])
    parser.add_argument('--epochs', '-e', type=int)
    parser.add_argument('--doc-length', '-l', type=int)
    parser.set_defaults(target_column='cefr', epochs=10, doc_length=500)
    return parser.parse_args()


def build_model(vocab_size: int, sequence_length: int, num_classes: int) -> Model:
    input_shape = (sequence_length,)
    input_layer = Input(shape=input_shape)
    embedding_layer = Embedding(vocab_size + 1, 20)(input_layer)
    pooled_feature_maps = []
    for kernel_size in [4, 5, 6]:
        conv_layer = Conv1D(
            filters=100, kernel_size=kernel_size, activation='relu')(embedding_layer)
        pooled_feature_maps.extend([
            GlobalAveragePooling1D()(conv_layer),
            GlobalMaxPooling1D()(conv_layer)
        ])
    merged = Concatenate()(pooled_feature_maps)
    dropout_layer = Dropout(0.5)(merged)
    output_layer = Dense(num_classes, activation='softmax')(dropout_layer)
    model = Model(inputs=input_layer, outputs=output_layer)
    model.compile('adam', 'categorical_crossentropy', metrics=['accuracy'])
    return model


def main():
    args = parse_args()
    seq_length = args.doc_length
    train, dev = load_train_and_dev()

    y_column = args.target_column
    labels = sorted(train[y_column].unique())
    print(labels)

    tokenizer = Tokenizer(lower=False)
    tokenizer.fit_on_texts(iter_all_tokens(train))
    vocab_size = len(tokenizer.index_word)
    print('vocab size = %d' % vocab_size)

    train_seqs = tokenizer.texts_to_sequences(iter_all_docs(train))
    dev_seqs = tokenizer.texts_to_sequences(iter_all_docs(dev))

    train_x = pad_sequences(train_seqs, maxlen=seq_length)
    dev_x = pad_sequences(dev_seqs, maxlen=seq_length)

    train_y = to_categorical([labels.index(c) for c in train[y_column]])
    dev_y = to_categorical([labels.index(c) for c in dev[y_column]])

    print(train_x.shape)
    print(dev_x.shape)

    model = build_model(vocab_size, seq_length, len(labels))
    model.summary()
    history = model.fit(
        train_x, train_y, epochs=20, batch_size=16,
        validation_data=(dev_x, dev_y), verbose=2)

    predictions = np.argmax(model.predict(dev_x), axis=1)
    gold = np.argmax(dev_y, axis=1)
    print(classification_report(gold, predictions, target_names=labels))
    print("== Confusion matrix ==")
    conf_matrix = confusion_matrix(gold, predictions)
    print(conf_matrix)
    heatmap(conf_matrix, labels, labels)
    plt.show()

    save_results('cnn_baseline', args.__dict__, history.history, predictions)


if __name__ == '__main__':
    main()