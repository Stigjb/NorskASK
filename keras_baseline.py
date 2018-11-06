from itertools import chain
from pathlib import Path

from keras.models import Model
from keras.layers import Embedding
from keras.layers import Input, Conv1D, Dropout, Dense, GlobalMaxPooling1D, Concatenate
from keras.preprocessing.text import Tokenizer
from keras.preprocessing.sequence import pad_sequences
from keras.utils import to_categorical

from utils import load_train_and_dev, conll_reader


def iter_all_tokens(train):
    for seq in iter_all_docs(train):
        for token in seq:
            yield token


def iter_all_docs(split):
    for filename in split.filename:
        filepath = Path('ASK/conll') / (filename + '.conll')
        cr = conll_reader(filepath)
        yield list(chain.from_iterable(cr))


def build_model(vocab_size: int, sequence_length: int) -> Model:
    input_shape = (sequence_length,)
    input_layer = Input(shape=input_shape)
    embedding_layer = Embedding(vocab_size + 1, 50)(input_layer)
    pooled_feature_maps = []
    for kernel_size in [4, 5, 6]:
        conv_layer = Conv1D(
            filters=100, kernel_size=kernel_size, activation='relu')(embedding_layer)
        pooling = GlobalMaxPooling1D()(conv_layer)
        pooled_feature_maps.append(pooling)
    merged = Concatenate()(pooled_feature_maps)
    dropout_layer = Dropout(0.5)(merged)
    output_layer = Dense(7, activation='softmax')(dropout_layer)
    model = Model(inputs=input_layer, outputs=output_layer)
    model.compile('adam', 'categorical_crossentropy', metrics=['accuracy'])
    return model


def main():
    seq_length = 100
    train, dev = load_train_and_dev()

    labels = sorted(list(train.cefr.unique()))
    print(labels)

    tokenizer = Tokenizer(lower=False)
    tokenizer.fit_on_texts(iter_all_tokens(train))
    vocab_size = len(tokenizer.index_word)
    print('vocab size = %d' % vocab_size)

    train_seqs = tokenizer.texts_to_sequences(iter_all_docs(train))
    dev_seqs = tokenizer.texts_to_sequences(iter_all_docs(dev))

    train_matrix = pad_sequences(train_seqs, maxlen=seq_length)
    dev_matrix = pad_sequences(dev_seqs, maxlen=seq_length)

    train_y = to_categorical([labels.index(c) for c in train.cefr])
    dev_y = to_categorical([labels.index(c) for c in dev.cefr])

    print(train_matrix.shape)
    print(dev_matrix.shape)

    model = build_model(vocab_size, seq_length)
    model.summary()
    model.fit(
        train_matrix, train_y, epochs=10, batch_size=8,
        validation_data=(dev_matrix, dev_y), verbose=2)


if __name__ == '__main__':
    main()
