import argparse
import os
import tempfile
from typing import Iterable, Sequence

from keras.layers import Dense, Dropout, Input
from keras.models import Model
from keras.optimizers import Adam
from keras.utils import to_categorical
import numpy as np
from sklearn.feature_extraction.text import CountVectorizer

from masterthesis.features.build_features import (
    bag_of_words, filename_iter, iterate_mixed_pos_docs, iterate_pos_docs
)
from masterthesis.models.callbacks import F1Metrics
from masterthesis.models.report import multi_task_report, report
from masterthesis.models.utils import add_common_args
from masterthesis.results import save_results
from masterthesis.utils import (
    AUX_OUTPUT_NAME, DATA_DIR, get_file_name, load_split, OUTPUT_NAME, REPRESENTATION_LAYER,
    rescale_regression_results, safe_plt as plt, save_model, set_reproducible
)


conll_folder = DATA_DIR / 'conll'


def parse_args():
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    parser.add_argument('featuretype', choices={'pos', 'bow', 'char', 'mix'})
    parser.add_argument('--lr', type=float, default=2e-4)
    parser.add_argument('--max-features', type=int, default=10000)
    parser.add_argument('--classification', action='store_true')
    return parser.parse_args()


def build_model(vocab_size: int, num_classes: Sequence[int], do_classification: bool):
    input_ = Input((vocab_size,))
    hidden_1 = Dense(100, activation='relu')(input_)
    dropout_1 = Dropout(0.5)(hidden_1)
    hidden_2 = Dense(256, activation='relu', name=REPRESENTATION_LAYER)(dropout_1)
    dropout_2 = Dropout(0.5)(hidden_2)
    if do_classification:
        output = Dense(num_classes[0], activation='softmax', name=OUTPUT_NAME)(dropout_2)
    else:
        output = Dense(1, activation='sigmoid', name=OUTPUT_NAME)(dropout_2)
    outputs = [output]
    if len(num_classes) > 1:
        aux_out = Dense(num_classes[1], activation='softmax', name=AUX_OUTPUT_NAME)(dropout_2)
        outputs.append(aux_out)
    return Model(inputs=[input_], outputs=outputs)


def pos_line_iter(split) -> Iterable[str]:
    for doc in iterate_pos_docs(split):
        yield ' '.join(doc)


def mixed_pos_line_iter(split) -> Iterable[str]:
    for doc in iterate_mixed_pos_docs(split):
        yield ' '.join(doc)


def preprocess(kind: str, max_features: int, train_meta, dev_meta):
    if kind == 'pos':
        vectorizer = CountVectorizer(
            lowercase=False, token_pattern=r"[^\s]+",
            ngram_range=(2, 4), max_features=max_features)
        train_x = vectorizer.fit_transform(pos_line_iter('train'))
        dev_x = vectorizer.transform(pos_line_iter('dev'))
        num_features = len(vectorizer.vocabulary_)
    elif kind == 'mix':
        vectorizer = CountVectorizer(
            lowercase=False, token_pattern=r"[^\s]+",
            ngram_range=(1, 3), max_features=max_features)
        train_x = vectorizer.fit_transform(mixed_pos_line_iter('train'))
        dev_x = vectorizer.transform(mixed_pos_line_iter('dev'))
        num_features = len(vectorizer.vocabulary_)
    elif kind == 'char':
        train_x, vectorizer = bag_of_words(
            'train', analyzer='char',
            ngram_range=(2, 4), max_features=max_features, lowercase=False)
        dev_x = vectorizer.transform(filename_iter(dev_meta))
        num_features = len(vectorizer.vocabulary_)
    elif kind == 'bow':
        train_x, vectorizer = bag_of_words(
            'train', token_pattern=r"[^\s]+", max_features=max_features, lowercase=False)
        dev_x = vectorizer.transform(filename_iter(dev_meta))
        num_features = len(vectorizer.vocabulary_)
    else:
        raise ValueError('Feature type "%s" is not supported' % kind)
    return train_x, dev_x, num_features


def main():
    args = parse_args()

    set_reproducible()

    do_classification = args.classification
    train_meta = load_split('train', round_cefr=args.round_cefr)
    dev_meta = load_split('dev', round_cefr=args.round_cefr)

    kind = args.featuretype
    train_x, dev_x, num_features = preprocess(kind, args.max_features, train_meta, dev_meta)

    cefr_labels = sorted(train_meta.cefr.unique())
    num_classes = [len(cefr_labels)]

    train_target_scores = np.array([cefr_labels.index(c) for c in train_meta.cefr])
    dev_target_scores = np.array([cefr_labels.index(c) for c in dev_meta.cefr])

    if do_classification:
        train_y = to_categorical(train_target_scores)
        dev_y = to_categorical(dev_target_scores)
    else:  # Regression
        highest_class = max(train_target_scores)
        train_y = np.array(train_target_scores) / highest_class
        dev_y = np.array(dev_target_scores) / highest_class

    train_y = [train_y]
    dev_y = [dev_y]

    multi_task = args.aux_loss_weight > 0
    if multi_task:
        lang_labels = sorted(train_meta.lang.unique())
        train_y.append(to_categorical([lang_labels.index(l) for l in train_meta.lang]))
        dev_y.append(to_categorical([lang_labels.index(l) for l in dev_meta.lang]))
        num_classes.append(len(lang_labels))
        loss_weights = {
            AUX_OUTPUT_NAME: args.aux_loss_weight,
            OUTPUT_NAME: 1.0 - args.aux_loss_weight
        }
    else:
        loss_weights = None

    print(num_classes)
    print(num_features)

    model = build_model(num_features, num_classes, do_classification)
    model.summary()
    if do_classification:
        optimizer = Adam(lr=args.lr)
        loss = 'categorical_crossentropy'
        metrics = ['accuracy']
    else:
        optimizer = 'rmsprop'
        loss = 'mean_squared_error'
        metrics = ['mae']
    model.compile(
        optimizer=optimizer,
        loss=loss,
        loss_weights=loss_weights,
        metrics=metrics)

    # Context manager fails on Windows (can't open an open file again)
    temp_handle, weights_path = tempfile.mkstemp(suffix='.h5')
    val_y = dev_y if do_classification else dev_target_scores
    callbacks = [F1Metrics(dev_x, val_y, weights_path)]
    history = model.fit(
        train_x, train_y, epochs=args.epochs, callbacks=callbacks, validation_data=(dev_x, dev_y),
        verbose=2)
    model.load_weights(weights_path)
    os.close(temp_handle)
    os.remove(weights_path)

    true = dev_target_scores
    if multi_task:
        predictions = model.predict(dev_x)[0]
    else:
        predictions = model.predict(dev_x)
    if do_classification:
        pred = np.argmax(predictions, axis=1)
    else:
        # Round to integers and clip to score range
        pred = rescale_regression_results(predictions, highest_class)
    if args.multi:
        multi_task_report(history.history, true, pred, cefr_labels)
    else:
        report(true, pred, cefr_labels)

    plt.show()

    prefix = 'mlp_%s' % args.featuretype
    fname = get_file_name(prefix)
    save_results(fname, args.__dict__, history.history, true, pred)

    if args.save_model:
        save_model(fname, model, None)


if __name__ == '__main__':
    main()
