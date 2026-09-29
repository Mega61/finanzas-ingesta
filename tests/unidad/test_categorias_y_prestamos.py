"""Los nombres: de una categoria nueva y de con quien fue un prestamo."""

from __future__ import annotations

import json

import pytest

from finanzas.adaptadores import ia
from finanzas.aplicacion import categorias, prestamos


@pytest.mark.parametrize(
    ('escrito', 'nombre'),
    [
        ('mascotas', 'Mascotas'),
        ('  Lavandería.', 'Lavandería'),
        ('regalos de navidad', 'Regalos de navidad'),
        ('fue lo del regalo de cumpleaños de la vecina', None),
        ('123456', None),
        ('', None),
    ],
)
def test_nombre_de_categoria(escrito, nombre):
    assert categorias.nombre_valido(escrito) == nombre


def test_ya_existe_sin_tildes_ni_mayusculas():
    assert categorias.ya_existe('lavanderia', ['Lavandería', 'Comida']) == 'Lavandería'
    assert categorias.ya_existe('Mascotas', ['Comida']) is None


@pytest.mark.parametrize(
    ('dicho', 'nombre'),
    [('mi jefe', 'Jefe'), ('a mi mamá', 'Mamá'), ('Laura', 'Laura'), ('', None)],
)
def test_nombre_de_prestamo(dicho, nombre):
    assert prestamos.nombre(dicho) == nombre


def _gemini(monkeypatch, respuesta):
    monkeypatch.setattr(
        ia.config, 'get', lambda k, d=None: 'k' if k == 'GEMINI_API_KEY' else d
    )
    visto = {}

    def llamar(payload):
        visto['payload'] = payload
        texto = json.dumps(respuesta)
        return {
            'candidates': [
                {'finishReason': 'STOP', 'content': {'parts': [{'text': texto}]}}
            ]
        }

    monkeypatch.setattr(ia, '_llamar', llamar)
    return visto


def test_la_ia_elige_de_las_existentes_por_enum(monkeypatch):
    visto = _gemini(monkeypatch, {'existente': 'Comida', 'nueva': '', 'razon': 'r'})
    d = ia.proponer_categoria({'valor': -1, 'contraparte': 'RAPPI'}, ['Comida', 'Gym'])
    assert (d['existente'], d['nueva']) == ('Comida', None)
    enum = visto['payload']['generationConfig']['responseSchema']['properties'][
        'existente'
    ]['enum']
    assert enum == ['Comida', 'Gym', 'ninguna']


def test_una_nueva_que_ya_existe_es_la_existente(monkeypatch):
    _gemini(monkeypatch, {'existente': 'ninguna', 'nueva': 'gym', 'razon': 'r'})
    d = ia.proponer_categoria({'valor': -1}, ['Comida', 'Gym'])
    assert (d['existente'], d['nueva']) == ('Gym', None)


def test_una_nueva_de_verdad(monkeypatch):
    _gemini(monkeypatch, {'existente': 'ninguna', 'nueva': 'Mascotas', 'razon': 'r'})
    d = ia.proponer_categoria({'valor': -1}, ['Comida'])
    assert (d['existente'], d['nueva']) == (None, 'Mascotas')
