"""
Calcul des indices de végétation (NDVI, NDWI, NDMI, SAVI, EVI) à partir de
Sentinel-2 (COPERNICUS/S2_SR_HARMONIZED) via Google Earth Engine.

Le paquet earthengine-api est importé de façon paresseuse : l'application
fonctionne même s'il n'est pas installé ; seules les routes de calcul en ont
besoin.

Authentification (à faire une seule fois sur la machine) :
    pip install earthengine-api
    earthengine authenticate
puis renseigner GEE_PROJECT dans config.py.
"""


def _ee():
    try:
        import ee  # noqa
    except ImportError as exc:
        raise RuntimeError(
            "Le paquet 'earthengine-api' n'est pas installé. "
            "Exécutez : pip install earthengine-api"
        ) from exc
    return ee


def init_ee(project):
    ee = _ee()
    try:
        ee.Initialize(project=project)
    except Exception:
        # Session non authentifiée
        raise RuntimeError(
            "Earth Engine n'est pas authentifié. Exécutez 'earthengine authenticate' "
            "puis vérifiez GEE_PROJECT dans config.py."
        )


def _mask_clouds(img):
    """Masque nuages / ombres / neige à partir de la bande SCL."""
    scl = img.select("SCL")
    mask = (
        scl.neq(3).And(scl.neq(8)).And(scl.neq(9))
        .And(scl.neq(10)).And(scl.neq(11))
    )
    return img.updateMask(mask)


def _collection(d1, d2, region, cloud_max):
    """
    Collection Sentinel-2 filtrée puis masquée (nuages par pixel).
    Repli : si le filtre de couverture nuageuse vide la collection
    (fréquent en hivernage), on garde toutes les scènes de la période.
    Retourne (collection_masquée, nb_scènes_brutes_getInfo-able).
    """
    ee = _ee()
    base = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate(d1, d2).filterBounds(region))
    filtre = base.filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_max))
    col = ee.ImageCollection(ee.Algorithms.If(filtre.size().gt(0), filtre, base))
    return col.map(_mask_clouds), base


def _add_indices(img):
    """Ajoute les 5 indices à partir des réflectances (mises à l'échelle 0..1)."""
    ee = _ee()
    N = img.select("B8").divide(10000)
    R = img.select("B4").divide(10000)
    G = img.select("B3").divide(10000)
    S1 = img.select("B11").divide(10000)
    B = img.select("B2").divide(10000)

    ndvi = N.subtract(R).divide(N.add(R)).rename("ndvi")
    ndwi = G.subtract(N).divide(G.add(N)).rename("ndwi")
    ndmi = N.subtract(S1).divide(N.add(S1)).rename("ndmi")
    savi = N.subtract(R).multiply(1.5).divide(N.add(R).add(0.5)).rename("savi")
    evi = (
        N.subtract(R).multiply(2.5)
        .divide(N.add(R.multiply(6)).subtract(B.multiply(7.5)).add(1))
        .rename("evi")
    )
    return ee.Image.cat([ndvi, ndwi, ndmi, savi, evi]).set(
        "system:time_start", img.get("system:time_start"))


def rgb_tiles(features, d1, d2, project, cloud_max=80):
    """
    Tuiles en vraies couleurs (RGB, bandes B4/B3/B2) découpées aux parcelles,
    pour visualiser la variabilité réelle du couvert. Retourne l'URL de tuiles XYZ.
    """
    ee = _ee()
    init_ee(project)
    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry(f["geometry"])) for f in features
    ])
    region = fc.geometry().bounds()
    col, base = _collection(d1, d2, region, cloud_max)
    if base.size().getInfo() == 0:
        raise RuntimeError(
            "Aucune image Sentinel-2 sur cette période/périmètre. "
            "Élargissez la fenêtre de dates."
        )
    img = col.median().select(["B4", "B3", "B2"]).clipToCollection(fc)
    vis = {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"]}
    return img.getMapId(vis)["tile_fetcher"].url_format


def index_tiles(features, d1, d2, project, indice="ndvi", cloud_max=80):
    """Raster d'un indice (médiane sur la période), découpé aux parcelles → URL de tuiles."""
    ee = _ee()
    init_ee(project)
    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry(f["geometry"])) for f in features
    ])
    region = fc.geometry().bounds()
    col, base = _collection(d1, d2, region, cloud_max)
    if base.size().getInfo() == 0:
        raise RuntimeError(
            "Aucune image Sentinel-2 sur cette période/périmètre. "
            "Élargissez la fenêtre de dates (commencez un mois plus tôt)."
        )
    img = col.median()
    if indice == "ndmi":
        band = img.normalizedDifference(["B8", "B11"])
    elif indice == "ndwi":
        band = img.normalizedDifference(["B3", "B8"])
    else:  # ndvi / savi / evi -> visuel NDVI
        band = img.normalizedDifference(["B8", "B4"])
    band = band.clipToCollection(fc)
    if indice == "ndmi":
        # Stress hydrique -> humidité : blanc(bleu très clair) -> bleu foncé (5 classes)
        vis = {"min": -0.10, "max": 0.40,
               "palette": ["deebf7", "9ecae1", "6baed6", "3182bd", "08519c"]}
    elif indice == "ndwi":
        # Eau : blanc (absence) -> bleu (présence)
        vis = {"min": -0.30, "max": 0.50, "palette": ["ffffff", "1565C0"]}
    else:
        # Santé végétation (NDVI) : rouge -> orange -> jaune -> vert clair -> vert foncé
        vis = {"min": 0.05, "max": 0.65,
               "palette": ["d73027", "F39C12", "F4D03F", "a6d96a", "1a9850"]}
    return band.getMapId(vis)["tile_fetcher"].url_format


def difference_tiles(features, da1, da2, db1, db2, project,
                     indice="ndvi", cloud_max=80):
    """
    Différence pixel-à-pixel (Sentinel-2, 10 m) de l'indice entre deux périodes.
    Retourne l'URL de tuiles XYZ (à afficher dans Leaflet) + les bornes de visualisation.
    """
    ee = _ee()
    init_ee(project)

    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry(f["geometry"])) for f in features
    ])
    region = fc.geometry().bounds()

    def composite(d1, d2):
        col, base = _collection(d1, d2, region, cloud_max)
        if base.size().getInfo() == 0:
            raise RuntimeError(
                "Aucune image Sentinel-2 sur l'une des deux périodes. "
                "Élargissez les fenêtres de dates."
            )
        img = col.median()
        if indice == "ndmi":
            return img.normalizedDifference(["B8", "B11"]).rename("v")
        return img.normalizedDifference(["B8", "B4"]).rename("v")  # NDVI

    diff = composite(db1, db2).subtract(composite(da1, da2)).clipToCollection(fc)
    vis = {
        "min": -0.3, "max": 0.3,
        "palette": ["a50026", "d73027", "fdae61", "ffffbf",
                    "a6d96a", "1a9850", "006837"],
    }
    mapid = diff.getMapId(vis)
    return mapid["tile_fetcher"].url_format


def compute_indices(features, date_start, date_end, project,
                    cloud_max=80, chunk=200):
    """
    features : liste de dicts {"id": <parcelle_id>, "geometry": <geojson WGS84>}
    Retourne : liste de dicts {"pid", "ndvi", "ndwi", "ndmi", "savi", "evi"}
    """
    ee = _ee()
    init_ee(project)

    resultats = []
    total_images = 0
    for i in range(0, len(features), chunk):
        lot = features[i:i + chunk]
        fc = ee.FeatureCollection([
            ee.Feature(ee.Geometry(f["geometry"]), {"pid": f["id"]})
            for f in lot
        ])
        col, base = _collection(date_start, date_end, fc.geometry(), cloud_max)
        n = base.size().getInfo()
        total_images += n
        if n == 0:
            # Aucune image sur ce périmètre : parcelles laissées sans valeur
            continue
        composite = _add_indices(col.median())
        stats = composite.reduceRegions(
            collection=fc, reducer=ee.Reducer.mean(), scale=10
        )
        for feat in stats.getInfo()["features"]:
            p = feat["properties"]
            resultats.append({
                "pid": p.get("pid"),
                "ndvi": p.get("ndvi"),
                "ndwi": p.get("ndwi"),
                "ndmi": p.get("ndmi"),
                "savi": p.get("savi"),
                "evi": p.get("evi"),
            })

    if total_images == 0:
        raise RuntimeError(
            "Aucune image Sentinel-2 disponible sur cette période/périmètre. "
            "Élargissez la fenêtre de dates (par exemple un début un mois plus tôt)."
        )
    return resultats


def compute_timeseries(features, date_start, date_end, project,
                       cloud_max=80, chunk=40):
    """Evolution des indices a chaque passage. Decoupage adaptatif (<5000 elements/requete)."""
    ee = _ee()
    init_ee(project)
    resultats = []
    compteur = {"images": 0}

    def _traiter(lot):
        if not lot:
            return
        fc = ee.FeatureCollection([
            ee.Feature(ee.Geometry(f["geometry"]), {"pid": f["id"]}) for f in lot
        ])
        col, base = _collection(date_start, date_end, fc.geometry(), cloud_max)
        try:
            n = base.size().getInfo()
        except Exception:
            n = 1
        if n == 0:
            return
        compteur["images"] += n
        col2 = col.map(_add_indices)

        def _par_image(img):
            d = img.date().format("YYYY-MM-dd")
            fc2 = (img.select(["ndvi", "ndwi", "ndmi", "savi", "evi"])
                   .reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=10))
            return fc2.map(lambda f: f.set("date", d))

        fcres = ee.FeatureCollection(col2.map(_par_image)).flatten()
        try:
            data = fcres.getInfo()["features"]
        except Exception as exc:
            msg = str(exc)
            if ("5000" in msg or "aborted" in msg.lower()) and len(lot) > 1:
                mid = len(lot) // 2
                _traiter(lot[:mid]); _traiter(lot[mid:]); return
            raise
        for feat in data:
            pr = feat["properties"]
            if pr.get("ndvi") is None:
                continue
            resultats.append({
                "pid": pr.get("pid"), "date": pr.get("date"),
                "ndvi": pr.get("ndvi"), "ndwi": pr.get("ndwi"),
                "ndmi": pr.get("ndmi"), "savi": pr.get("savi"), "evi": pr.get("evi"),
            })

    for i in range(0, len(features), chunk):
        _traiter(features[i:i + chunk])

    if compteur["images"] == 0:
        raise RuntimeError("Aucune image Sentinel-2 sur cette periode/perimetre. Elargissez la fenetre de dates.")
    return resultats


def thumb_url(features, d1, d2, project, indice="rgb", dimensions=1000, cloud_max=80):
    """URL d'une image PNG (Earth Engine) de la parcelle : RGB, NDVI ou NDMI (composite médian)."""
    ee = _ee()
    init_ee(project)
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry(f["geometry"])) for f in features])
    region = fc.geometry()
    col, base = _collection(d1, d2, region.bounds(), cloud_max)
    if base.size().getInfo() == 0:
        raise RuntimeError("Aucune image Sentinel-2 sur cette période/périmètre.")
    img = col.median()
    if indice == "ndmi":
        b = img.normalizedDifference(["B8", "B11"])
        vis = b.visualize(min=-0.10, max=0.40,
                          palette=["deebf7", "9ecae1", "6baed6", "3182bd", "08519c"])
    elif indice == "ndvi":
        b = img.normalizedDifference(["B8", "B4"])
        vis = b.visualize(min=0.05, max=0.65,
                          palette=["d73027", "F39C12", "F4D03F", "a6d96a", "1a9850"])
    else:  # rgb
        vis = img.select(["B4", "B3", "B2"]).visualize(min=0, max=3000)
    vis = vis.clipToCollection(fc)
    coords = region.bounds().getInfo()["coordinates"]
    return vis.getThumbURL({"region": coords, "dimensions": dimensions, "format": "png"})
