"""El enrutamiento del comando `finanzas`.

Antes eran cuatro archivos con su propio `if __name__ == '__main__'`, cada uno
con opciones distintas y ninguno mencionando a los otros. El CLI no reimplementa
nada: enruta. Lo que se prueba aqui es justo eso — que cada palabra llegue al
modulo correcto con los argumentos intactos — sin ejecutar nada de verdad.
"""

from __future__ import annotations

import pytest

from finanzas import cli


@pytest.fixture
def enrutado(monkeypatch):
    """Reemplaza el main() de cada modulo por un espia, para verificar a donde
    llego la llamada sin bajar correo ni escribir en Firefly."""
    llamadas: list[tuple[str, list[str]]] = []

    def falso(nombre):
        def _main(argv=None):
            llamadas.append((nombre, list(argv or [])))
            return 0

        return _main

    import conciliador
    import demonio
    import servicio
    import verificar

    for mod, nombre in (
        (demonio, 'demonio'),
        (servicio, 'servicio'),
        (conciliador, 'conciliador'),
        (verificar, 'verificar'),
    ):
        monkeypatch.setattr(mod, 'main', falso(nombre))
    return llamadas


class TestAyuda:
    def test_sin_argumentos_muestra_la_ayuda_y_sale_bien(self, capsys):
        """Correr `finanzas` a secas no es un error: es la forma de averiguar
        que se puede hacer."""
        assert cli.main([]) == 0
        assert 'Uso: finanzas' in capsys.readouterr().out

    @pytest.mark.parametrize('bandera', ['-h', '--help', 'ayuda'])
    def test_las_tres_formas_de_pedir_ayuda(self, capsys, bandera):
        assert cli.main([bandera]) == 0
        assert 'Uso: finanzas' in capsys.readouterr().out

    def test_la_ayuda_lista_todo_lo_que_se_puede_correr(self, capsys):
        cli.main([])
        salida = capsys.readouterr().out
        for accion, _ in cli.ACCIONES_DEMONIO:
            assert accion in salida, f'{accion} no aparece en la ayuda'
        for grupo in cli.GRUPOS:
            assert grupo in salida, f'{grupo} no aparece en la ayuda'

    def test_avisa_que_nada_publica_sin_en_serio(self, capsys):
        """Es la garantia que hace seguro probar comandos a ciegas."""
        cli.main([])
        assert '--en-serio' in capsys.readouterr().out

    def test_un_comando_desconocido_da_codigo_2_y_escribe_en_stderr(self, capsys):
        assert cli.main(['inventado']) == 2
        cap = capsys.readouterr()
        assert 'inventado' in cap.err
        assert cap.out == '', 'un error no va por la salida normal'


class TestNoHayAmbiguedad:
    def test_ninguna_palabra_esta_en_las_dos_listas(self):
        """`conciliar` estaba en las dos y el grupo ganaba, dejando la accion
        del demonio inalcanzable sin que nada lo dijera."""
        acciones = {a for a, _ in cli.ACCIONES_DEMONIO}
        assert acciones & set(cli.GRUPOS) == set()

    def test_ningun_comando_choca_con_una_bandera(self):
        reservadas = {'-h', '--help', 'ayuda', 'version', '--version'}
        todos = {a for a, _ in cli.ACCIONES_DEMONIO} | set(cli.GRUPOS)
        assert todos & reservadas == set()

    def test_todo_grupo_apunta_a_un_modulo_con_main(self):
        for grupo, (modulo, _, _) in cli.GRUPOS.items():
            mod = __import__(modulo)
            assert callable(getattr(mod, 'main', None)), (
                f'{grupo} apunta a {modulo}, que no tiene main()'
            )

    def test_todo_grupo_tiene_descripcion_y_ejemplos(self):
        for grupo, (_, desc, ejemplos) in cli.GRUPOS.items():
            assert desc and ejemplos, f'{grupo} sin describir'


class TestEnrutamiento:
    def test_una_accion_del_dia_a_dia_va_al_demonio(self, enrutado):
        assert cli.main(['estado']) == 0
        assert enrutado == [('demonio', ['estado'])]

    def test_las_opciones_pasan_intactas(self, enrutado):
        """El CLI no reinterpreta ni valida las opciones: se las entrega tal
        cual, para no tener que mantener dos copias de cada una."""
        cli.main(['ciclo', '--en-serio', '--tope', '50'])
        assert enrutado == [('demonio', ['ciclo', '--en-serio', '--tope', '50'])]

    @pytest.mark.parametrize(
        ('comando', 'modulo'),
        [
            ('servicio', 'servicio'),
            ('bot', 'bot'),
            ('conciliar', 'conciliador'),
            ('revisar', 'verificar'),
        ],
    )
    def test_cada_grupo_llega_a_su_modulo(self, enrutado, monkeypatch, comando, modulo):
        if modulo == 'bot':
            import bot

            monkeypatch.setattr(
                bot,
                'main',
                lambda argv=None: enrutado.append(('bot', list(argv or []))) or 0,
            )
        cli.main([comando, 'algo'])
        assert enrutado == [(modulo, ['algo'])]

    def test_el_grupo_sin_argumentos_no_le_pasa_nada(self, enrutado):
        cli.main(['revisar'])
        assert enrutado == [('verificar', [])]

    def test_version_no_toca_ningun_modulo(self, enrutado, capsys):
        """Preguntar la version no puede abrir la base ni salir a la red: es lo
        primero que se corre cuando algo esta raro."""
        assert cli.main(['version']) == 0
        assert enrutado == []
        assert 'finanzas' in capsys.readouterr().out

    def test_version_reporta_el_commit_de_la_imagen(self, monkeypatch, capsys):
        """Sin esto no hay forma de saber si el contenedor esta corriendo codigo
        viejo, que ya paso."""
        monkeypatch.setenv('GIT_SHA', 'abcdef1234567890')
        monkeypatch.setenv('BUILD_FECHA', '2026-09-01T00:00:00Z')
        cli.main(['version'])
        salida = capsys.readouterr().out
        assert 'abcdef123456' in salida
        assert '2026-09-01' in salida

    def test_devuelve_el_codigo_de_salida_del_modulo(self, monkeypatch):
        """Si el modulo falla, el CLI tiene que fallar tambien: de eso depende
        que el contenedor se reinicie y que el CI se ponga rojo."""
        import demonio

        monkeypatch.setattr(demonio, 'main', lambda argv=None: 3)
        assert cli.main(['estado']) == 3

    def test_un_modulo_que_no_devuelve_nada_cuenta_como_exito(self, monkeypatch):
        """Varios main() terminan sin `return`, o sea devuelven None."""
        import demonio

        monkeypatch.setattr(demonio, 'main', lambda argv=None: None)
        assert cli.main(['estado']) == 0
