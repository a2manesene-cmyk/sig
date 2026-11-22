"""
Module Sol — analyses de sol des parcelles.

- PointSolIFDC : points de prélèvement de la base IFDC (Dundel Suuf), géoréférencés.
- AnalyseSol   : fiche sol d'une parcelle (une par parcelle). Valeurs interpolées depuis
                 les points IFDC, ou saisies/corrigées par le CAR (origine = "CAR").
- HistoriqueSol : traçabilité des saisies et corrections (qui, quand, quoi).
"""
import json
from datetime import datetime

from geoalchemy2 import Geometry

from app.extensions import db


class PointSolIFDC(db.Model):
    __tablename__ = "sol_points_ifdc"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)   # ex. FK-10007-G1
    x_utm = db.Column(db.Float)
    y_utm = db.Column(db.Float)
    geom = db.Column(Geometry(geometry_type="POINT", srid=4326))          # affichage carte
    geom_utm = db.Column(Geometry(geometry_type="POINT", srid=32628))     # distances (m)

    ph = db.Column(db.Float)
    p_olsen = db.Column(db.Float)
    k = db.Column(db.Float)
    ca = db.Column(db.Float)
    mg = db.Column(db.Float)
    cec = db.Column(db.Float)
    n = db.Column(db.Float)
    c_total = db.Column(db.Float)
    s = db.Column(db.Float)
    zn = db.Column(db.Float)
    b = db.Column(db.Float)
    sable = db.Column(db.Float)
    argile = db.Column(db.Float)
    limon = db.Column(db.Float)

    valeurs_ecartees = db.Column(db.Text)   # JSON {param: valeur brute} hors plage plausible
    source = db.Column(db.String(60), default="IFDC Dundel Suuf")
    importe_le = db.Column(db.DateTime, default=datetime.utcnow)


class AnalyseSol(db.Model):
    __tablename__ = "analyses_sol"

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
                            unique=True, nullable=False, index=True)
    parcelle = db.relationship("Parcelle", backref=db.backref("analyse_sol", uselist=False))

    # Paramètres (mêmes unités que la base IFDC)
    ph = db.Column(db.Float)          # pH eau
    p_olsen = db.Column(db.Float)     # ppm
    k = db.Column(db.Float)           # ppm
    ca = db.Column(db.Float)          # ppm
    mg = db.Column(db.Float)          # ppm
    cec = db.Column(db.Float)         # meq/100 g
    n = db.Column(db.Float)           # %
    c_total = db.Column(db.Float)     # %
    mo = db.Column(db.Float)          # % (= C x 1,724 si non mesurée)
    s = db.Column(db.Float)           # ppm
    zn = db.Column(db.Float)          # ppm
    b = db.Column(db.Float)           # ppm
    sable = db.Column(db.Float)       # %
    argile = db.Column(db.Float)      # %
    limon = db.Column(db.Float)       # %
    texture = db.Column(db.String(40))
    fertilite = db.Column(db.String(20))      # Faible / Moyenne / Bonne
    contraintes = db.Column(db.String(200))   # ex. "P faible; MO faible"

    # Provenance
    origine = db.Column(db.String(10), default="IFDC")   # IFDC | CAR
    source = db.Column(db.String(60))                    # IFDC (interpolé) / Analyse labo / Kit terrain / Observation terrain
    laboratoire = db.Column(db.String(120))
    date_analyse = db.Column(db.Date)
    note = db.Column(db.Text)

    # Rattachement IFDC (conservé même après correction par le CAR)
    fiabilite = db.Column(db.String(30))      # Bonne (< 2 km) / Indicative (2–5 km) / Hors couverture IFDC
    distance_m = db.Column(db.Float)          # distance au point IFDC le plus proche
    nb_points = db.Column(db.Integer)         # points IFDC utilisés dans l'interpolation
    ifdc_json = db.Column(db.Text)            # valeurs IFDC interpolées (pour retour arrière)

    saisi_par_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    saisi_par = db.relationship("User")
    saisi_le = db.Column(db.DateTime)
    maj_le = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def ifdc(self):
        try:
            return json.loads(self.ifdc_json) if self.ifdc_json else {}
        except ValueError:
            return {}


class HistoriqueSol(db.Model):
    __tablename__ = "analyses_sol_historique"

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    user = db.relationship("User")
    date = db.Column(db.DateTime, default=datetime.utcnow)
    action = db.Column(db.String(40))      # Saisie CAR / Retour aux valeurs IFDC
    detail = db.Column(db.Text)            # JSON {param: [avant, après]}

    @property
    def changements(self):
        try:
            return json.loads(self.detail) if self.detail else {}
        except ValueError:
            return {}
