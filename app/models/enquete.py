"""
Données d'enquête des parcelles (import GeoJSON QField / KoBo).

- PratiqueCampagne : itinéraire technique et rendement d'une parcelle pour une campagne
                     (variété, semis, fumure, labour, rendement mesuré). Base du moteur de conseil.
- AttributParcelle : autres informations d'enquête (foncier, exhaure, équipements…),
                     conservées telles quelles pour consultation.
"""
from datetime import datetime

from app.extensions import db


class PratiqueCampagne(db.Model):
    __tablename__ = "pratiques_campagne"
    __table_args__ = (db.UniqueConstraint("parcelle_id", "annee", name="uq_pratique_parcelle_annee"),)

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    parcelle = db.relationship("Parcelle", backref=db.backref(
        "pratiques", lazy="dynamic", cascade="all, delete-orphan"))
    annee = db.Column(db.Integer, nullable=False, index=True)

    culture = db.Column(db.String(80))
    precedent = db.Column(db.String(80))           # précédent cultural
    variete = db.Column(db.String(80), index=True)
    date_semis = db.Column(db.Date)
    labour = db.Column(db.Boolean)
    matiere_organique = db.Column(db.Boolean)
    uree_sacs = db.Column(db.Float)                # sacs de 50 kg déclarés
    npk_sacs = db.Column(db.Float)
    rendement_t_ha = db.Column(db.Float)           # valeur saisie
    rendement_valide = db.Column(db.Boolean)       # dans la plage plausible pour la culture
    methode_rendement = db.Column(db.String(60))   # Pose carré rendement / estimation producteur…
    date_recolte = db.Column(db.Date)
    observations = db.Column(db.Text)
    source = db.Column(db.String(60), default="Enquête GeoJSON")
    maj_le = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def uree_kg_ha(self):
        s = self.parcelle.surface_ha if self.parcelle else None
        return self.uree_sacs * 50 / s if self.uree_sacs is not None and s else None

    @property
    def npk_kg_ha(self):
        s = self.parcelle.surface_ha if self.parcelle else None
        return self.npk_sacs * 50 / s if self.npk_sacs is not None and s else None


class AttributParcelle(db.Model):
    __tablename__ = "parcelles_attributs"
    __table_args__ = (db.UniqueConstraint("parcelle_id", "cle", name="uq_attribut_parcelle_cle"),)

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    cle = db.Column(db.String(80), nullable=False)
    valeur = db.Column(db.String(255))
    maj_le = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SourceParcelle(db.Model):
    """Identifiant de l'entité d'origine (couche + fid du fichier GeoJSON) : rend les réimports sûrs."""
    __tablename__ = "parcelles_sources"

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    source_id = db.Column(db.String(255), unique=True, nullable=False)
