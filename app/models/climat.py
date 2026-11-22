"""
Module Climat — données climatiques journalières par cellule de 0,05° (~5 km, grille CHIRPS).

- CelluleClimat     : cellule de la grille CHIRPS couvrant au moins une parcelle.
- ParcelleCellule   : rattachement parcelle -> cellule (centre de la parcelle).
- ClimatJournalier  : pluie (CHIRPS), Tmin/Tmax (ERA5-Land), ETP (Hargreaves) par jour.
- SaisonClimat      : indicateurs de la saison des pluies par cellule et par année.
"""
from datetime import datetime

from geoalchemy2 import Geometry

from app.extensions import db


class CelluleClimat(db.Model):
    __tablename__ = "climat_cellules"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)   # "i_j" de la grille
    lon = db.Column(db.Float, nullable=False)    # centre de la cellule
    lat = db.Column(db.Float, nullable=False)
    geom = db.Column(Geometry(geometry_type="POINT", srid=4326))
    ru_mm = db.Column(db.Float)                  # réserve utile retenue (selon la texture dominante)
    texture = db.Column(db.String(40))           # texture dominante des parcelles de la cellule


class ParcelleCellule(db.Model):
    __tablename__ = "climat_parcelles"

    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
                            primary_key=True)
    cellule_id = db.Column(db.Integer, db.ForeignKey("climat_cellules.id", ondelete="CASCADE"),
                           index=True, nullable=False)
    cellule = db.relationship("CelluleClimat")


class ClimatJournalier(db.Model):
    __tablename__ = "climat_journalier"
    __table_args__ = (db.UniqueConstraint("cellule_id", "date", name="uq_climat_cellule_date"),)

    id = db.Column(db.Integer, primary_key=True)
    cellule_id = db.Column(db.Integer, db.ForeignKey("climat_cellules.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    pluie_mm = db.Column(db.Float)       # CHIRPS
    tmin = db.Column(db.Float)           # °C, ERA5-Land
    tmax = db.Column(db.Float)           # °C, ERA5-Land
    et0_mm = db.Column(db.Float)         # évapotranspiration de référence (Hargreaves)


class SaisonClimat(db.Model):
    __tablename__ = "climat_saisons"
    __table_args__ = (db.UniqueConstraint("cellule_id", "annee", name="uq_saison_cellule_annee"),)

    id = db.Column(db.Integer, primary_key=True)
    cellule_id = db.Column(db.Integer, db.ForeignKey("climat_cellules.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    cellule = db.relationship("CelluleClimat")
    annee = db.Column(db.Integer, nullable=False, index=True)

    debut = db.Column(db.Date)               # démarrage agronomique de la saison
    fin = db.Column(db.Date)                 # fin (épuisement de la réserve en eau du sol)
    longueur_j = db.Column(db.Integer)
    cumul_mm = db.Column(db.Float)           # pluie entre début et fin (ou dernière donnée)
    cumul_mai_oct = db.Column(db.Float)      # cumul 1er mai – 31 octobre
    nb_jours_pluie = db.Column(db.Integer)   # jours >= 1 mm pendant la saison
    seq_seche_max_j = db.Column(db.Integer)  # plus longue séquence sèche pendant la saison
    seq_seche_max_debut = db.Column(db.Date)
    nb_seq_seches_10j = db.Column(db.Integer)
    faux_departs = db.Column(db.Integer)     # pluies utiles suivies d'une longue sécheresse
    derniere_donnee = db.Column(db.Date)
    complete = db.Column(db.Boolean, default=False)   # fin de saison observée
    calcule_le = db.Column(db.DateTime, default=datetime.utcnow)
