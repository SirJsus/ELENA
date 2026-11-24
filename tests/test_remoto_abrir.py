"""«Ver este video»: solo videos de YouTube, con la URL armada de nuevo, y el comando sin shell."""

import pytest

from elena.modelo import ErrorElena
from elena.remoto.abrir import comando, url_de_youtube

VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


@pytest.mark.parametrize("texto, esperado", [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", VIDEO),
    ("http://youtube.com/watch?v=jNQXAC9IVRw&feature=share&si=abc", VIDEO),
    ("https://m.youtube.com/watch?app=desktop&v=jNQXAC9IVRw", VIDEO),
    ("  https://youtu.be/jNQXAC9IVRw?si=xyz  ", VIDEO),
    ("youtu.be/jNQXAC9IVRw", VIDEO),
    ("https://WWW.YouTube.com/watch?v=jNQXAC9IVRw", VIDEO),
    ("https://www.youtube.com/shorts/jNQXAC9IVRw", VIDEO),
    ("https://www.youtube.com/live/jNQXAC9IVRw?feature=shared", VIDEO),
    ("https://www.youtube.com/embed/jNQXAC9IVRw", VIDEO),
    ("https://youtu.be/jNQXAC9IVRw?t=90", VIDEO + "&t=90s"),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=1h2m3s", VIDEO + "&t=3723s"),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=2m", VIDEO + "&t=120s"),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=raro", VIDEO),
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PL590L5WQmH8fJ54F369BLDSqIwcs-TCfs",
     VIDEO + "&list=PL590L5WQmH8fJ54F369BLDSqIwcs-TCfs"),
    ("https://music.youtube.com/watch?v=jNQXAC9IVRw&list=RDAMVM",
     "https://music.youtube.com/watch?v=jNQXAC9IVRw&list=RDAMVM"),
])
def test_enlaces_validos(texto, esperado):
    assert url_de_youtube(texto) == esperado


@pytest.mark.parametrize("texto", [
    "",
    "   ",
    "https://ejemplo.com/watch?v=jNQXAC9IVRw",
    "https://youtube.com.evil.example/watch?v=jNQXAC9IVRw",
    "https://evil.example/?u=https://youtu.be/jNQXAC9IVRw",
    "https://notyoutube.com/watch?v=jNQXAC9IVRw",
    "https://usuario:clave@www.youtube.com/watch?v=jNQXAC9IVRw",
    "https://www.youtube.com:8443/watch?v=jNQXAC9IVRw",
    "javascript:alert(1)//youtube.com/watch?v=jNQXAC9IVRw",
    "file:///home/x/youtube.com/watch?v=jNQXAC9IVRw",
    "ftp://www.youtube.com/watch?v=jNQXAC9IVRw",
    "https://www.youtube.com/watch?v=corto",
    "https://www.youtube.com/watch?v=jNQXAC9IVRw123",
    "https://www.youtube.com/watch?v=jNQXAC9IV%22w",
    "https://www.youtube.com/playlist?list=PL590L5WQmH8fJ54F369BLDSqIwcs-TCfs",
    "https://www.youtube.com/@canal",
    "https://youtu.be/",
    "https://youtu.be/jNQXAC9IVRw/otra",
    "https://[::1/watch?v=jNQXAC9IVRw",
    "https://www.youtube.com/watch?v=" + "a" * 3000,
])
def test_enlaces_invalidos(texto):
    with pytest.raises(ErrorElena):
        url_de_youtube(texto)


def test_la_lista_rara_no_pasa():
    assert url_de_youtube(VIDEO + "&list=a b&c=d") == VIDEO
    assert url_de_youtube(VIDEO + "&list=" + "x" * 100) == VIDEO


def test_comando(monkeypatch):
    monkeypatch.delenv("ELENA_REMOTO_ABRIR", raising=False)
    assert comando(VIDEO) == ["xdg-open", VIDEO]
    monkeypatch.setenv("ELENA_REMOTO_ABRIR", "'/ruta con espacios/abrir.sh' --nueva {url}")
    assert comando(VIDEO) == ["/ruta con espacios/abrir.sh", "--nueva", VIDEO]
    monkeypatch.setenv("ELENA_REMOTO_ABRIR", "zen-browser --new-tab")            # sin {url}: al final
    assert comando(VIDEO) == ["zen-browser", "--new-tab", VIDEO]
    assert comando(VIDEO, "abrir --url={url}") == ["abrir", f"--url={VIDEO}"]
    # Un ; en la URL no llega a ninguna shell: es parte del mismo argumento
    assert comando("https://x/;rm -rf ~", "abrir {url}") == ["abrir", "https://x/;rm -rf ~"]
    with pytest.raises(ErrorElena):
        comando(VIDEO, "abrir 'sin cerrar")
    with pytest.raises(ErrorElena):
        comando(VIDEO, "   ")
